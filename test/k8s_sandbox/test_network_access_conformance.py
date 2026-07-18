from pathlib import Path

import pytest
import yaml
from inspect_ai.util import DomainPort, NetworkAccess

from k8s_sandbox._helm import DEFAULT_CHART, StaticValuesSource
from k8s_sandbox._network_access import (
    NetworkAccessValuesSource,
    helm_values_to_network_access,
    network_access_to_helm_values,
)
from k8s_sandbox._sandbox_environment import _create_values_source, _ResolvedConfig


def test_to_helm_values_omits_empty_lists() -> None:
    assert network_access_to_helm_values(NetworkAccess(allow_domains=["pypi.org"])) == {
        "allowDomains": ["pypi.org"]
    }


def test_to_helm_values_full() -> None:
    na = NetworkAccess(
        allow_domains=["pypi.org", "*.debian.org"],
        allow_domains_ports=[DomainPort(port=22, domain="pypi.org")],
        allow_cidr=["1.1.1.1/32"],
        allow_entities=["world"],
    )

    assert network_access_to_helm_values(na) == {
        "allowDomains": ["pypi.org", "*.debian.org"],
        "allowDomainsPorts": [{"port": 22, "domain": "pypi.org"}],
        "allowCIDR": ["1.1.1.1/32"],
        "allowEntities": ["world"],
    }


def test_round_trip_values_to_na_to_values() -> None:
    values = {
        "allowDomains": ["pypi.org", "github.com"],
        "allowDomainsPorts": [{"port": 8008}, {"port": 22, "domain": "github.com"}],
        "allowCIDR": ["10.0.0.0/8"],
        "allowEntities": ["world"],
    }

    na = helm_values_to_network_access(values)

    assert network_access_to_helm_values(na) == values


def test_lowercase_protocol_normalizes_to_uppercase() -> None:
    values = {
        "allowDomains": ["pypi.org"],
        "allowDomainsPorts": [{"port": 22}, {"port": 443, "protocol": "udp"}],
    }

    na = helm_values_to_network_access(values)

    assert na.allow_domains_ports[1].protocol == "UDP"
    assert network_access_to_helm_values(na)["allowDomainsPorts"] == [
        {"port": 22},
        {"port": 443, "protocol": "UDP"},
    ]


def test_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="not one of TCP, UDP, ANY"):
        helm_values_to_network_access(
            {
                "allowDomains": ["pypi.org"],
                "allowDomainsPorts": [{"port": 22, "protocol": "sctp"}],
            }
        )


def _resolved(values: Path | None, chart: Path | None = None) -> _ResolvedConfig:
    return _ResolvedConfig(
        chart=chart,
        values=values,
        context=None,
        default_user=None,
        restarted_container_behavior="warn",
        max_pod_ops=None,
    )


def test_values_source_normalizes_egress_and_preserves_other_keys(tmp_path):
    src_file = tmp_path / "values.yaml"
    src_file.write_text(
        yaml.safe_dump(
            {
                "services": {"default": {"image": "python:3.12-bookworm"}},
                "allowDomains": ["pypi.org"],
                "allowDomainsPorts": [{"port": 22}, {"port": 443, "protocol": "udp"}],
            }
        )
    )

    with NetworkAccessValuesSource(src_file).values_file() as out:
        rendered = yaml.safe_load(Path(out).read_text())

    # Non-egress keys pass through untouched; egress keys are canonicalized (no
    # domain: null; protocol emitted only when non-default and uppercased).
    assert rendered["services"] == {"default": {"image": "python:3.12-bookworm"}}
    assert rendered["allowDomains"] == ["pypi.org"]
    assert rendered["allowDomainsPorts"] == [
        {"port": 22},
        {"port": 443, "protocol": "UDP"},
    ]


def test_create_values_source_wraps_builtin_chart_values(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text("allowDomains:\n  - pypi.org\n")

    source = _create_values_source(_resolved(values, chart=None))

    assert isinstance(source, NetworkAccessValuesSource)


def test_create_values_source_leaves_custom_chart_untouched(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text("allowDomains:\n  - pypi.org\n")
    chart = tmp_path / "chart"
    chart.mkdir()

    source = _create_values_source(_resolved(values, chart=chart))

    assert isinstance(source, StaticValuesSource)
    assert not isinstance(source, NetworkAccessValuesSource)


def test_create_values_source_none_values_is_static(tmp_path):
    source = _create_values_source(_resolved(None, chart=None))

    assert isinstance(source, StaticValuesSource)


@pytest.mark.parametrize(
    "values_file",
    [
        # Existing fixtures, one per egress field (and a couple of combinations).
        "helm_chart/resources/allow-domains-values.yaml",  # allowDomains (+ SNI/Host)
        # allowDomainsPorts (udp)
        "helm_chart/resources/allow-domains-ports-values.yaml",
        # scoped domain port
        "helm_chart/resources/allow-domains-ports-scoped-values.yaml",
        # allowDomains ["*"]
        "helm_chart/resources/allow-domains-wildcard-all-values.yaml",
        "resources/netpol-values.yaml",  # allowDomains + allowCIDR
        "resources/netpol-world-values.yaml",  # allowEntities
    ],
)
def test_na_roundtrip_renders_identical_cnp(values_file: str, tmp_path: Path) -> None:
    from test.k8s_sandbox.helm_chart.test_helm_chart import (
        _get_documents,
        _run_helm_template,
    )

    baseline_values = Path(__file__).parent / values_file
    direct = _run_helm_template(DEFAULT_CHART, baseline_values)

    values = yaml.safe_load(baseline_values.read_text())
    na = helm_values_to_network_access(values)
    for key in ("allowDomains", "allowDomainsPorts", "allowCIDR", "allowEntities"):
        values.pop(key, None)
    values.update(network_access_to_helm_values(na))
    rewritten = tmp_path / "values.yaml"
    rewritten.write_text(yaml.safe_dump(values))
    through_na = _run_helm_template(DEFAULT_CHART, rewritten)

    def egress_cnp(docs: list) -> dict:
        return next(
            d
            for d in _get_documents(docs, "CiliumNetworkPolicy")
            if d["metadata"]["name"].endswith("-sandbox-egress")
        )

    # Compare the FULL egress CiliumNetworkPolicy document (metadata + spec), not just
    # spec.egress, so any drift in the rendered policy is caught.
    assert egress_cnp(direct) == egress_cnp(through_na)
