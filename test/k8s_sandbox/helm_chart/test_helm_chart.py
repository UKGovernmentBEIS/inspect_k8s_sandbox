import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import k8s_sandbox


@pytest.fixture
def chart_dir() -> Path:
    k8s_src = Path(k8s_sandbox.__file__).parent.resolve()
    return k8s_src / "resources" / "helm" / "agent-env"


@pytest.fixture
def test_resources_dir() -> Path:
    return Path(__file__).parent.resolve() / "resources"


def test_default_chart(chart_dir: Path) -> None:
    documents = _run_helm_template(chart_dir)

    services = _get_documents(documents, "StatefulSet")
    assert services[0]["metadata"]["name"] == "agent-env-my-release-default"
    assert (
        services[0]["spec"]["template"]["spec"]["containers"][0]["image"]
        == "python:3.12-bookworm"
    )


def test_additional_resources(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "additional-resources-values.yaml"
    )

    secrets = _get_documents(documents, "Secret")
    assert secrets[0]["metadata"]["name"] == "my-first-secret"
    assert secrets[1]["metadata"]["name"] == "my-second-secret"


def test_multiple_ports(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "multiple-ports-values.yaml"
    )

    services = _get_documents(documents, "Service")
    service = next(
        service for service in services if "coredns" not in service["metadata"]["name"]
    )
    # When multiple ports are defined, each port must have a name or helm install fails.
    assert service["spec"]["ports"] == [
        {"name": "port-80", "port": 80, "protocol": "TCP"},
        {"name": "port-81", "port": 81, "protocol": "TCP"},
    ]


def test_volumes(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "volumes-values.yaml"
    )

    # Verify PVCs.
    pvcs = _get_documents(documents, "PersistentVolumeClaim")
    assert len(pvcs) == 3
    assert pvcs[0]["metadata"]["name"] == "agent-env-my-release-custom-volume"
    assert pvcs[0]["spec"]["resources"]["requests"]["storage"] == "42Mi"
    assert pvcs[1]["metadata"]["name"] == "agent-env-my-release-simple-volume-1"
    assert pvcs[2]["metadata"]["name"] == "agent-env-my-release-simple-volume-2"
    # Verify StatefulSet volume and volumeMounts.
    expected_volume_mounts = yaml.safe_load("""
- mountPath: /manual-volume-mount-path
  name: manual-volume
- mountPath: /simple-volume-mount-path
  name: agent-env-my-release-simple-volume-1
- mountPath: /etc/resolv.conf
  name: resolv-conf
  subPath: resolv.conf
""")
    expected_volumes = yaml.safe_load("""
- name: coredns-config
  configMap:
    name: agent-env-my-release-coredns-configmap
- name: resolv-conf
  configMap:
    name: agent-env-my-release-resolv-conf
- emptyDir: {}
  name: manual-volume
- name: agent-env-my-release-simple-volume-1
  persistentVolumeClaim:
    claimName: agent-env-my-release-simple-volume-1
""")
    services = _get_documents(documents, "StatefulSet")
    assert len(services) == 2
    for service in services:
        template_spec = service["spec"]["template"]["spec"]
        assert template_spec["containers"][0]["volumeMounts"] == expected_volume_mounts
        assert template_spec["volumes"] == expected_volumes


def test_annotations(chart_dir: Path, test_resources_dir: Path) -> None:
    attr_value = "my=!:. '\"value"

    documents = _run_helm_template(
        chart_dir,
        test_resources_dir / "volumes-values.yaml",
        f"annotations.myValue={attr_value}",
    )

    for stateful_set in _get_documents(documents, "StatefulSet"):
        assert stateful_set["metadata"]["annotations"]["myValue"] == attr_value
        template = stateful_set["spec"]["template"]
        assert template["metadata"]["annotations"]["myValue"] == attr_value
    for network_policy in _get_documents(documents, "NetworkPolicy"):
        assert network_policy["metadata"]["annotations"]["myValue"] == attr_value
    for pvc in _get_documents(documents, "PersistentVolumeClaim"):
        assert pvc["metadata"]["annotations"]["myValue"] == attr_value
    for service in _get_documents(documents, "Service"):
        assert service["metadata"]["annotations"]["myValue"] == attr_value
    for deployment in _get_documents(documents, "Deployment"):
        assert deployment["metadata"]["annotations"]["myValue"] == attr_value


def test_resource_requests_and_limits(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "multiple-services-values.yaml"
    )

    stateful_sets = _get_documents(documents, "StatefulSet")
    assert len(stateful_sets) == 2
    for item in stateful_sets:
        container = item["spec"]["template"]["spec"]["containers"][0]
        assert "resources" in container
        assert "limits" in container["resources"]
        assert "requests" in container["resources"]


def test_dns_records_and_ports(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "dns-record-values.yaml"
    )

    services = _get_documents(documents, "Service")
    headless_services = [s for s in services if "coredns" not in s["metadata"]["name"]]
    assert len(headless_services) == 3
    assert all(service["spec"]["clusterIP"] == "None" for service in headless_services)
    # a does not get a service.
    b = headless_services[0]
    assert b["metadata"]["name"] == "agent-env-my-release-b"
    assert "ports" not in b["spec"]
    c = headless_services[1]
    assert c["metadata"]["name"] == "agent-env-my-release-c"
    assert "ports" not in c["spec"]
    d = headless_services[2]
    assert d["metadata"]["name"] == "agent-env-my-release-d"
    assert d["spec"]["ports"] == [{"name": "port-80", "port": 80, "protocol": "TCP"}]


def test_quotes_env_var_values(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "env-types-values.yaml"
    )

    stateful_sets = _get_documents(documents, "StatefulSet")
    env = stateful_sets[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    # Verify that the env var values are quoted (i.e. strings). Helm install fails
    # if env var values are not strings (even if the values.yaml file used strings).
    assert env[1] == {"name": "A", "value": "1"}
    assert env[2] == {"name": "B", "value": "2"}
    assert env[3] == {"name": "C", "value": "three"}


def test_unset_magic_string(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "unset-runtime-class-values.yaml"
    )

    stateful_sets = _get_documents(documents, "StatefulSet")
    assert "runtimeClassName" not in stateful_sets[0]["spec"]["template"]["spec"]
    assert "runtimeClassName" not in stateful_sets[1]["spec"]["template"]["spec"]
    assert (
        stateful_sets[2]["spec"]["template"]["spec"]["runtimeClassName"]
        == "my-runtime-class-name"
    )


def _run_helm_template(
    chart_dir: Path, values_file: Path | None = None, set_str: str | None = None
) -> list[dict[str, Any]]:
    cmd = [
        "helm",
        "template",
        "my-release",
        str(chart_dir),
    ]
    if values_file:
        cmd += ["-f", str(values_file)]
    if set_str:
        cmd += ["--set", set_str]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    return list(yaml.safe_load_all(result.stdout))


def _get_documents(documents: list[Any], doc_type_filter: str) -> list[dict[str, Any]]:
    return [doc for doc in documents if doc["kind"] == doc_type_filter]
