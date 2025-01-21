import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

# A prototype script to convert from a Docker compose.yaml file into a helm-values.yaml
# file suitable for the built-in Helm chart.
# Feels like this is missing a design pattern.

# Documentation to include elsewhere:
# - This is by no means a complete conversion script.
# - It only supports basic Docker Compose features.

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class HelmService(BaseModel):
    image: str | None = None
    command: list[str] | str | None = None
    workingDir: str | None = None
    dnsRecord: bool
    env: list[dict[str, str]] = []
    volumes: list[str] | list[dict] | None = None
    readinessProbe: dict[str, Any] = {}
    resources: dict[str, Any] = {}


class HelmValues(BaseModel):
    services: dict[str, HelmService] = {}
    volumes: dict[str, Any] = {}


def main() -> None:
    samples = Path(__file__).parent / "samples"
    convert(samples / "compose.yaml", samples / "helm-values.yaml")


def convert(compose_path: Path, helm_values_path: Path) -> None:
    compose = yaml.safe_load(compose_path.read_text())
    helm = HelmValues()
    for key, service in compose["services"].items():
        helm.services[key] = convert_service(key, service)
    # TODO: Consider adding support for x-allowDomains.
    if volumes := compose.get("volumes"):
        helm.volumes = volumes
    helm_values_yaml = yaml.dump(
        helm.model_dump(exclude_defaults=True), sort_keys=False
    )
    print(helm_values_yaml)
    helm_values_path.write_text(helm_values_yaml)


def convert_service(name: str, compose_service: dict[str, Any]) -> HelmService:
    result: dict[str, Any] = dict()
    result["image"] = compose_service.pop("image", None)
    if command := compose_service.pop("command", None):
        result["command"] = command
    if workdir := compose_service.pop("working_dir", None):
        result["workingDir"] = workdir
    # Create a DNS record for every service (default in Docker Compose).
    result["dnsRecord"] = True
    if env := compose_service.pop("environment", None):
        result["env"] = convert_env(env)
    if volumes := compose_service.pop("volumes", None):
        result["volumes"] = volumes
    if healthcheck := compose_service.pop("healthcheck", None):
        result["readinessProbe"] = convert_healthcheck_to_readiness_probe(healthcheck)
    result.update(convert_deploy(compose_service.pop("deploy", {})))
    if compose_service.pop("expose", None):
        logger.warning(f"Ignoring 'expose' key in service '{name}'.")
    if compose_service.pop("init", None):
        logger.warning(f"Ignoring 'init' key in service '{name}'.")
    if unsupported := get_keys(compose_service):
        raise ValueError(f"Unsupported keys {unsupported} in service '{name}'.")
    return HelmService(**result)


def convert_env(compose_env: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for key, value in compose_env.items():
        result.append({"name": key, "value": value})
    return result


def convert_deploy(compose_deploy: dict[str, Any]) -> dict[str, Any]:
    result = {}
    if resources := compose_deploy.pop("resources", None):
        result["resources"] = convert_resources(resources)
    if unsupported := get_keys(compose_deploy):
        raise ValueError(f"Unsupported keys in deploy: {unsupported}")
    return result


def convert_resources(compose_resources: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    limits = compose_resources.get("limits")
    if limits:
        result["limits"] = convert_resource(limits)
    reservations = compose_resources.get("reservations")
    if reservations:
        result["requests"] = convert_resource(reservations)
    return result


def convert_resource(original: dict[str, Any]) -> dict[str, Any]:
    result = {}
    if cpu := original.pop("cpus", None):
        result["cpu"] = cpu
    if memory := original.pop("memory", None):
        result["memory"] = convert_memory(memory)
    if original:
        raise ValueError(f"Unrecognised keys in 'resource': {original}")
    return result


def convert_memory(memory: str) -> str:
    """Convert Docker memory format (e.g., '512m', '1g') to Ki/Mi/Gi."""

    def convert_unit(unit: str) -> str:
        match unit.lower():
            case "b":
                return ""
            case "k":
                return "Ki"
            case "m":
                return "Mi"
            case "g":
                return "Gi"
            case _:
                raise ValueError()

    r = r"(\d+)(b|[mkg])b?"
    m = re.match(r, memory, re.IGNORECASE)
    if not m:
        raise ValueError(f"Unrecognised memory value: {memory}")
    return f"{m.group(1)}{convert_unit(m.group(2))}"


def convert_healthcheck_to_readiness_probe(
    compose_healthcheck: dict[str, Any],
) -> dict[str, Any]:
    """Assume that healthchecks are to be mapped to readiness probes."""
    result: dict[str, Any] = {}
    result["exec"] = convert_healthcheck_test_to_exec(compose_healthcheck.pop("test"))
    if interval := compose_healthcheck.pop("interval", None):
        result["periodSeconds"] = convert_duration_to_seconds(interval)
    if timeout := compose_healthcheck.pop("timeout", None):
        result["timeoutSeconds"] = convert_duration_to_seconds(timeout)
    if retries := compose_healthcheck.pop("retries", None):
        result["failureThreshold"] = retries
    if start_period := compose_healthcheck.pop("start_period", None):
        result["initialDelaySeconds"] = convert_duration_to_seconds(start_period)
    if start_interval := compose_healthcheck.pop("start_interval", None):
        result["failureThreshold"] = convert_duration_to_seconds(start_interval)
    if unsupported := get_keys(compose_healthcheck):
        raise ValueError(f"Unsupported keys in healthcheck: {unsupported}")
    return result


def convert_healthcheck_test_to_exec(test: list[str]) -> dict[str, Any]:
    if test[0] == "CMD":
        return {"command": test[1:]}
    if test[0] == "CMD-SHELL":
        return {"command": ["/bin/sh", "-c", test[1]]}
    raise ValueError(f"Unsupported healthcheck test: {test}")


def convert_duration_to_seconds(duration: str) -> int:
    """Convert Docker duration format (e.g., '30s', '1m') to seconds."""
    if duration.endswith("s"):
        return int(duration[:-1])
    elif duration.endswith("m"):
        return int(duration[:-1]) * 60
    elif duration.endswith("h"):
        return int(duration[:-1]) * 3600
    else:
        raise ValueError(f"Unsupported duration format: {duration}")


def get_keys(dict: dict[str, Any], ignore: set[str] = set()) -> set[str]:
    return set(dict) - ignore


if __name__ == "__main__":
    main()
