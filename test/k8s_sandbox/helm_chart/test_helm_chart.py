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


def test_no_service_account_by_default(chart_dir: Path) -> None:
    documents = _run_helm_template(chart_dir)

    assert _get_documents(documents, "ServiceAccount") == []
    for stateful_set in _get_documents(documents, "StatefulSet"):
        spec = stateful_set["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is False
        assert "serviceAccountName" not in spec


def test_service_account_name(chart_dir: Path) -> None:
    documents = _run_helm_template(chart_dir, set_str="serviceAccountName=my-sa")

    service_accounts = _get_documents(documents, "ServiceAccount")
    assert len(service_accounts) == 1
    assert service_accounts[0]["metadata"]["name"] == "my-sa"
    assert "app.kubernetes.io/name" in service_accounts[0]["metadata"]["labels"]

    for stateful_set in _get_documents(documents, "StatefulSet"):
        spec = stateful_set["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is False
        assert spec["serviceAccountName"] == "my-sa"


def test_service_account_token_automount_requires_opt_in(chart_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, set_str="automountServiceAccountToken=true"
    )

    for stateful_set in _get_documents(documents, "StatefulSet"):
        spec = stateful_set["spec"]["template"]["spec"]
        assert spec["automountServiceAccountToken"] is True


def test_init_containers(chart_dir: Path, test_resources_dir: Path) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "services-with-init-container.yaml"
    )

    stateful_sets = _get_documents(documents, "StatefulSet")

    expected_bar_name = "agent-env-my-release-bar"
    bar_sets = [s for s in stateful_sets if s["metadata"]["name"] == expected_bar_name]
    assert bar_sets, f"No StatefulSet named {expected_bar_name} found"

    bar_spec = bar_sets[0]["spec"]["template"]["spec"]

    assert "initContainers" in bar_spec, "bar must have initContainers"
    init_containers = bar_spec["initContainers"]
    assert len(init_containers) == 1

    wait_ic = init_containers[0]
    assert wait_ic["name"] == "wait-for-foo-connectivity"
    assert "command" in wait_ic

    cmd_str = " ".join(wait_ic["command"])
    assert "nc -z -v -w5" in cmd_str
    assert "sleep" in cmd_str

    container = bar_spec["containers"][0]
    assert "command" in container
    assert len(container["command"]) > 0


def test_init_containers_with_all_fields(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    """Test init containers support resources, securityContext, etc."""
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "init-container-enhanced-values.yaml"
    )

    stateful_sets = _get_documents(documents, "StatefulSet")

    expected_app_name = "agent-env-my-release-app"
    app_sets = [s for s in stateful_sets if s["metadata"]["name"] == expected_app_name]
    assert app_sets, f"No StatefulSet named {expected_app_name} found"

    app_spec = app_sets[0]["spec"]["template"]["spec"]

    assert "initContainers" in app_spec, "app must have initContainers"
    init_containers = app_spec["initContainers"]
    assert len(init_containers) == 1

    init_container = init_containers[0]
    assert init_container["name"] == "init-with-all-fields"
    assert init_container["image"] == "busybox:1.36"

    # Test imagePullPolicy
    assert init_container["imagePullPolicy"] == "Always"

    # Test workingDir
    assert init_container["workingDir"] == "/tmp"

    # Test resources
    assert "resources" in init_container
    assert init_container["resources"]["limits"]["memory"] == "64Mi"
    assert init_container["resources"]["limits"]["cpu"] == "50m"
    assert init_container["resources"]["requests"]["memory"] == "64Mi"
    assert init_container["resources"]["requests"]["cpu"] == "50m"

    # Test securityContext
    assert "securityContext" in init_container
    assert init_container["securityContext"]["runAsUser"] == 1000
    assert init_container["securityContext"]["runAsNonRoot"] is True
    assert init_container["securityContext"]["allowPrivilegeEscalation"] is False

    # Test command and args
    assert init_container["command"] == ["sh", "-c", 'echo "Init with all fields"']
    assert init_container["args"] == ["--verbose"]

    # Test AGENT_ENV is still injected
    assert "env" in init_container
    agent_env_var = next(
        (e for e in init_container["env"] if e["name"] == "AGENT_ENV"), None
    )
    assert agent_env_var is not None
    assert "agent-env-my-release" in agent_env_var["value"]


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


def test_allow_domains_egress_enforces_identity_on_pinned_ips(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "allow-domains-values.yaml"
    )

    cnps = _get_documents(documents, "CiliumNetworkPolicy")
    egress_policy = next(
        cnp for cnp in cnps if cnp["metadata"]["name"].endswith("-sandbox-egress")
    )
    fqdn_rules = [rule for rule in egress_policy["spec"]["egress"] if "toFQDNs" in rule]
    assert len(fqdn_rules) == 1
    fqdn_rule = fqdn_rules[0]

    allow_domains = ["pypi.org", "*.debian.org"]
    assert [entry["matchPattern"] for entry in fqdn_rule["toFQDNs"]] == allow_domains

    # Egress to pinned IPs is constrained to 80/443, and the request identity must
    # match an allowed domain, so a shared-CDN IP cannot be reused to reach off-list
    # origins: the TLS SNI on 443, and the HTTP Host header on 80.
    by_port = {tp["ports"][0]["port"]: tp for tp in fqdn_rule["toPorts"]}
    assert set(by_port) == {"443", "80"}

    assert by_port["443"]["ports"] == [{"port": "443", "protocol": "TCP"}]
    assert by_port["443"]["serverNames"] == allow_domains

    # The glob is translated to a case-insensitive, port-tolerant anchored regex
    # for the Host header.
    assert by_port["80"]["ports"] == [{"port": "80", "protocol": "TCP"}]
    hosts = [rule["host"] for rule in by_port["80"]["rules"]["http"]]
    assert hosts == [
        "(?i)^pypi[.]org(:[0-9]+)?$",
        "(?i)^[^.]+[.]debian[.]org(:[0-9]+)?$",
    ]


def test_allow_domains_ports_opens_extra_ports_ip_pinned(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "allow-domains-ports-values.yaml"
    )

    egress_policy = next(
        cnp
        for cnp in _get_documents(documents, "CiliumNetworkPolicy")
        if cnp["metadata"]["name"].endswith("-sandbox-egress")
    )
    fqdn_rule = next(r for r in egress_policy["spec"]["egress"] if "toFQDNs" in r)

    # The extra ports are the toPorts entry with neither serverNames nor http
    # rules (IP-pinned only). protocol defaults to ANY; 443 may be added as UDP.
    extra = next(
        tp
        for tp in fqdn_rule["toPorts"]
        if "serverNames" not in tp and "rules" not in tp
    )
    assert [(p["port"], p["protocol"]) for p in extra["ports"]] == [
        ("22", "ANY"),
        ("443", "UDP"),
    ]


def test_allow_domains_ports_rejects_identity_bypassing_ports(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    # Listing 80/443 with anything but UDP would disable the SNI/Host check, so
    # the chart must refuse to render.
    with pytest.raises(subprocess.CalledProcessError):
        _run_helm_template(
            chart_dir, test_resources_dir / "allow-domains-ports-invalid-values.yaml"
        )


def test_allow_domains_ports_scopes_to_a_single_domain(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "allow-domains-ports-scoped-values.yaml"
    )

    egress = next(
        cnp
        for cnp in _get_documents(documents, "CiliumNetworkPolicy")
        if cnp["metadata"]["name"].endswith("-sandbox-egress")
    )["spec"]["egress"]
    fqdn_rules = [r for r in egress if "toFQDNs" in r]

    # The shared rule (the one carrying the SNI/Host identity checks) takes the
    # unscoped port; the scoped port becomes its own single-domain rule.
    shared = next(
        r for r in fqdn_rules if any("serverNames" in tp for tp in r["toPorts"])
    )
    scoped = [r for r in fqdn_rules if r is not shared]

    shared_extra = next(
        tp for tp in shared["toPorts"] if "serverNames" not in tp and "rules" not in tp
    )
    assert [(p["port"], p["protocol"]) for p in shared_extra["ports"]] == [
        ("8008", "ANY")
    ]

    assert len(scoped) == 1
    assert [m["matchPattern"] for m in scoped[0]["toFQDNs"]] == ["github.com"]
    assert scoped[0]["toPorts"] == [{"ports": [{"port": "22", "protocol": "ANY"}]}]


def test_allow_domains_ports_rejects_unlisted_scope_domain(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    # A scoped domain that is not in allowDomains would never resolve, so the
    # chart must refuse to render rather than emit an inert rule.
    with pytest.raises(subprocess.CalledProcessError):
        _run_helm_template(
            chart_dir, test_resources_dir / "allow-domains-ports-bad-domain-values.yaml"
        )


def test_allow_domains_wildcard_all_skips_identity_enforcement(
    chart_dir: Path, test_resources_dir: Path
) -> None:
    documents = _run_helm_template(
        chart_dir, test_resources_dir / "allow-domains-wildcard-all-values.yaml"
    )

    egress = next(
        cnp
        for cnp in _get_documents(documents, "CiliumNetworkPolicy")
        if cnp["metadata"]["name"].endswith("-sandbox-egress")
    )["spec"]["egress"]
    fqdn_rule = next(r for r in egress if "toFQDNs" in r)

    # "*" (allow all) has no valid serverNames form, so no identity-enforcing
    # toPorts is emitted -- egress to all resolved IPs is permitted on all ports.
    assert [m["matchPattern"] for m in fqdn_rule["toFQDNs"]] == ["*"]
    assert "toPorts" not in fqdn_rule


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
