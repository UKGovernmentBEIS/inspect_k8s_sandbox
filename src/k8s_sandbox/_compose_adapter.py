import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def convert(compose_path: Path) -> dict[str, Any]:
    compose = yaml.safe_load(compose_path.read_text())
    helm: dict[str, Any] = dict(services={})
    for key, service in compose["services"].items():
        try:
            helm["services"][key] = convert_service(key, service)
        except ValueError as e:
            raise ValueError(f"Error converting service '{key}'.") from e
    # TODO: Consider adding support for x-allowDomains.
    if volumes := compose.get("volumes"):
        helm["volumes"] = volumes
    return helm


def convert_service(name: str, compose_service: dict[str, Any]) -> dict[str, Any]:
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
    mem_limit = compose_service.pop("mem_limit", None)
    result.update(convert_deploy(compose_service.pop("deploy", {}), mem_limit))
    if user := compose_service.pop("user", None):
        result["securityContext"] = convert_user_to_security_context(user)
    if compose_service.pop("expose", None):
        logger.info(
            f"Ignoring 'expose' key in service '{name}': all ports are open in K8s."
        )
    if compose_service.pop("init", None):
        logger.info(f"Ignoring 'init' key in service '{name}': not supported in K8s.")
    if unsupported := get_keys(compose_service):
        raise ValueError(f"Unsupported keys {unsupported} in service '{name}'.")
    return result


def convert_env(compose_env: dict[str, Any] | list[str]) -> list[dict[str, str]]:
    result = []
    if isinstance(compose_env, dict):
        for key, value in compose_env.items():
            result.append({"name": key, "value": value})
    elif isinstance(compose_env, list):
        for item in compose_env:
            if "=" not in item:
                raise ValueError(f"Invalid environment variable: {item}")
            key, value = item.split("=", maxsplit=1)
            result.append({"name": key, "value": value})
    else:
        raise ValueError(
            f"Invalid environment format. Expected dict or list but got "
            f"{type(compose_env)}."
        )
    return result


def convert_deploy(
    compose_deploy: dict[str, Any], mem_limit: str | None
) -> dict[str, Any]:
    result = {}
    if resources := compose_deploy.pop("resources", None):
        result["resources"] = convert_resources(resources)
        if mem_limit:
            logger.warning(
                f"Ignoring 'mem_limit: {mem_limit}' because deploy.resources is set "
                "which takes precedence."
            )
    elif mem_limit:
        result["resources"] = {"limits": {"memory": convert_memory(mem_limit)}}
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


def convert_user_to_security_context(user: str) -> dict[str, Any]:
    if isinstance(user, str) and ":" in user:
        uid, gid = user.split(":", maxsplit=1)
        return {"runAsUser": int(uid), "runAsGroup": int(gid)}
    return {"runAsUser": int(user)}


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
