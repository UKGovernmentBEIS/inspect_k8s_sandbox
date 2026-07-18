from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Literal

import yaml
from inspect_ai.util import DomainPort, NetworkAccess

from k8s_sandbox._helm import ValuesSource

_EGRESS_KEYS = ("allowDomains", "allowDomainsPorts", "allowCIDR", "allowEntities")


def _normalize_protocol(value: Any) -> Literal["TCP", "UDP", "ANY"]:
    """Canonicalize a Helm protocol value to DomainPort's uppercase form."""
    text = value if value is not None else "ANY"
    if not isinstance(text, str):
        raise ValueError(
            f"allowDomainsPorts protocol must be a string or absent, got {value!r}."
        )

    match text.upper():
        case "TCP":
            return "TCP"
        case "UDP":
            return "UDP"
        case "ANY":
            return "ANY"
        case _:
            raise ValueError(
                f"allowDomainsPorts protocol {value!r} is not one of TCP, UDP, ANY."
            )


def network_access_to_helm_values(na: NetworkAccess) -> dict[str, Any]:
    """Serialize NetworkAccess to the Helm values the chart consumes."""
    values: dict[str, Any] = {}

    if na.allow_domains:
        values["allowDomains"] = list(na.allow_domains)

    if na.allow_domains_ports:
        ports: list[dict[str, Any]] = []
        for domain_port in na.allow_domains_ports:
            port: dict[str, Any] = {"port": domain_port.port}
            if domain_port.protocol != "ANY":
                port["protocol"] = domain_port.protocol
            if domain_port.domain is not None:
                port["domain"] = domain_port.domain
            ports.append(port)
        values["allowDomainsPorts"] = ports

    if na.allow_cidr:
        values["allowCIDR"] = list(na.allow_cidr)

    if na.allow_entities:
        values["allowEntities"] = list(na.allow_entities)

    return values


def helm_values_to_network_access(values: dict[str, Any]) -> NetworkAccess:
    """Normalize Helm egress values into NetworkAccess."""
    return NetworkAccess(
        allow_domains=list(values.get("allowDomains", [])),
        allow_domains_ports=[
            DomainPort(
                port=port["port"],
                protocol=_normalize_protocol(port.get("protocol")),
                domain=port.get("domain"),
            )
            for port in values.get("allowDomainsPorts", [])
        ],
        allow_cidr=list(values.get("allowCIDR", [])),
        allow_entities=list(values.get("allowEntities", [])),
    )


class NetworkAccessValuesSource(ValuesSource):
    """Normalize Helm values egress keys via NetworkAccess.

    Only the four egress keys are round-tripped through NetworkAccess (validating them
    and canonicalizing to the omitted-key/uppercase form the chart renders); every
    other key is passed through untouched. Used only for the built-in chart — a custom
    chart's values are opaque and must not be rewritten.
    """

    def __init__(self, file: Path) -> None:
        self._file = file

    @contextmanager
    def values_file(self) -> Generator[Path, None, None]:
        values = yaml.safe_load(self._file.read_text()) or {}
        na = helm_values_to_network_access(values)
        normalized = network_access_to_helm_values(na)
        for key in _EGRESS_KEYS:
            values.pop(key, None)
        values.update(normalized)
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(yaml.dump(values, sort_keys=False))
            f.flush()
            yield Path(f.name)
