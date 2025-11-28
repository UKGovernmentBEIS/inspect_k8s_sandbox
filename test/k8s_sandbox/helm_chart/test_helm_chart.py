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


def test_templated_additional_resources_inline(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir,
        test_resources_dir / "additional-resources-template-inline-values.yaml",
    )

    secrets = _get_documents(documents, "Secret")
    assert len(secrets) == 1
    assert (
        secrets[0]["metadata"]["name"] == "agent-env-my-release-object-templated-secret"
    )
    assert (
        secrets[0]["metadata"]["labels"]["app.kubernetes.io/instance"] == "my-release"
    )


def test_templated_additional_resources_block(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir,
        test_resources_dir / "additional-resources-template-block-values.yaml",
    )

    cnps = _get_documents(documents, "CiliumNetworkPolicy")
    target = "agent-env-my-release-sandbox-default-external-ingress"
    custom_policy = next(
        (cnp for cnp in cnps if cnp["metadata"]["name"] == target), None
    )
    assert custom_policy is not None

    # Verify selector labels were rendered
    selector_labels = custom_policy["spec"]["endpointSelector"]["matchLabels"]
    assert selector_labels["app.kubernetes.io/name"] == "agent-env"
    assert selector_labels["app.kubernetes.io/instance"] == "my-release"


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


@pytest.mark.parametrize(
    "labels",
    [
        pytest.param({}, id="no-labels"),
        pytest.param({"myLabel": "test-label"}, id="one-label"),
        pytest.param(
            {"myLabel": "test-label", "myOtherLabel": "test-other-label"},
            id="two-labels",
        ),
        pytest.param(
            {"labelWithColon": "a: b"},
            id="label-with-colon",
        ),
    ],
)
def test_labels(
    chart_dir: Path, test_resources_dir: Path, labels: dict[str, str]
) -> None:
    set_str = ",".join(f"labels.{key}={value}" for key, value in labels.items())
    documents = _run_helm_template(
        chart_dir,
        test_resources_dir / "volumes-values.yaml",
        set_str,
    )

    for stateful_set in _get_documents(documents, "StatefulSet"):
        assert labels.items() <= stateful_set["metadata"]["labels"].items()
        template = stateful_set["spec"]["template"]
        assert labels.items() <= template["metadata"]["labels"].items()
    for network_policy in _get_documents(documents, "NetworkPolicy"):
        assert labels.items() <= network_policy["metadata"]["labels"].items()
    for pvc in _get_documents(documents, "PersistentVolumeClaim"):
        assert labels.items() <= pvc["metadata"]["labels"].items()
    for service in _get_documents(documents, "Service"):
        assert labels.items() <= service["metadata"]["labels"].items()
    for deployment in _get_documents(documents, "Deployment"):
        assert labels.items() <= deployment["metadata"]["labels"].items()


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


def test_cluster_default_magic_string(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "cluster-default-runtime-values.yaml"
    )

    stateful_sets = _get_documents(documents, "StatefulSet")
    assert "runtimeClassName" not in stateful_sets[0]["spec"]["template"]["spec"]
    assert "runtimeClassName" not in stateful_sets[1]["spec"]["template"]["spec"]
    assert (
        stateful_sets[2]["spec"]["template"]["spec"]["runtimeClassName"]
        == "my-runtime-class-name"
    )


@pytest.mark.parametrize(
    ("overrides", "expected_coredns_image", "expected_coredns_command"),
    [
        (
            {
                "image": "public.ecr.aws/eks-distro/coredns/coredns:v1.8.3-eks-1-20-22",
                "command": ["/special-dns-command", "special-dns-arg"],
            },
            "public.ecr.aws/eks-distro/coredns/coredns:v1.8.3-eks-1-20-22",
            ["/special-dns-command", "special-dns-arg"],
        ),
        (
            {
                "image": "public.ecr.aws/eks-distro/coredns/coredns:v1.8.3-eks-1-20-22",
            },
            "public.ecr.aws/eks-distro/coredns/coredns:v1.8.3-eks-1-20-22",
            ["/coredns", "-conf", "/etc/coredns/Corefile"],
        ),
        (
            {
                "command": ["/special-dns-command"],
            },
            "coredns/coredns:1.8.3",
            ["/special-dns-command"],
        ),
    ],
)
def test_coredns_container(
    chart_dir: Path,
    overrides: dict[str, Any],
    expected_coredns_image: str,
    expected_coredns_command: list[str],
) -> None:
    set_str_parts: list[str] = []
    if "image" in overrides:
        set_str_parts.append(f"corednsImage={overrides['image']}")
    if "command" in overrides:
        set_str_parts.extend(
            [
                f"corednsCommand[{idx_cmd}]={cmd}"
                for idx_cmd, cmd in enumerate(overrides["command"])
            ]
        )
    documents = _run_helm_template(chart_dir, set_str=",".join(set_str_parts))

    stateful_sets = _get_documents(documents, "StatefulSet")
    assert len(stateful_sets) == 1
    corends_container = next(
        (
            container
            for container in stateful_sets[0]["spec"]["template"]["spec"]["containers"]
            if container["name"] == "coredns"
        ),
        None,
    )
    assert corends_container is not None
    assert corends_container["image"] == expected_coredns_image
    assert corends_container["command"] == expected_coredns_command


def test_network_isolated_service(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "network-isolated-values.yaml"
    )

    cnps = _get_documents(documents, "CiliumNetworkPolicy")

    # Verify isolate-service has isolate policy
    isolate_policy = next(
        (cnp for cnp in cnps if cnp["metadata"]["name"].endswith("-isolate")), None
    )
    assert isolate_policy is not None
    assert (
        isolate_policy["metadata"]["name"]
        == "agent-env-my-release-svc-isolated-service-isolate"
    )
    # ingressDeny and egressDeny deny all traffic from/to all entities
    assert isolate_policy["spec"]["ingressDeny"] == [{"fromEntities": ["all"]}]
    assert isolate_policy["spec"]["egressDeny"] == [{"toEntities": ["all"]}]

    # Verify normal-service doesn't have isolate policy
    normal_service_policies = [
        cnp for cnp in cnps if "normal-service" in cnp["metadata"]["name"]
    ]
    assert len(normal_service_policies) == 1
    assert "isolate" not in normal_service_policies[0]["metadata"]["name"]

    normal_spec = normal_service_policies[0]["spec"]
    assert normal_spec.get("ingress") != []
    assert normal_spec.get("egress") != []


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
