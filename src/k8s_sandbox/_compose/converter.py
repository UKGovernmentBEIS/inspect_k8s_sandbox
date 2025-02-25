import logging
import re
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)


class ComposeConverterError(Exception):
    """An error raised when converting a Docker Compose file to Helm values."""

    pass


def convert_compose_to_helm_values(compose_path: Path) -> dict[str, Any]:
    """Convert a Docker Compose file to Helm values.

    The resulting Helm values file is suitable for the built-in Helm chart.

    This is by no means a comprehensive conversion. It only supports a small subset
    of commonly used Docker Compose functionality.

    Returns:
        A dictionary representing the Helm values.
    """
    compose = yaml.safe_load(compose_path.read_text())
    helm: dict[str, Any] = dict()
    if services := compose.get("services"):
        helm["services"] = _convert_services(services)
    if volumes := compose.get("volumes"):
        helm["volumes"] = volumes
    # TODO: Consider adding support for x-allowDomains.
    return helm


def _convert_services(compose_services: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    for service_name, service_value in compose_services.items():
        try:
            result[service_name] = _convert_service(service_name, service_value)
        except Exception as e:
            # Raise a new exception with additional context.
            raise ComposeConverterError(
                f"Error converting service '{service_name}'."
            ) from e
    return result


def _transform(
    src: dict[str, Any],
    src_key: str,
    dst: dict[str, Any],
    dst_key: str,
    # Default is identity function.
    fn: Callable = lambda x: x,
) -> None:
    """
    Transfers a key from src to dst, applying a function to the value.

    The key is removed from src if it exists.
    """
    value = src.pop(src_key, None)
    if value is not None:
        dst[dst_key] = fn(value)


def _convert_service(name: str, src: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    # Ordered as per Helm chart values.yaml documentation.
    _transform(src, "image", result, "image")
    _transform(src, "entrypoint", result, "command", _str_to_list)
    _transform(src, "command", result, "args", _str_to_list)
    _transform(src, "working_dir", result, "workingDir")
    # Create a DNS record for every service (same behaviour as Docker Compose).
    result["dnsRecord"] = True
    _transform(src, "environment", result, "env", _convert_env)
    _transform(src, "volumes", result, "volumes")
    _transform(
        src, "healthcheck", result, "readinessProbe", _healthcheck_to_readiness_probe
    )
    mem_limit = src.pop("mem_limit", None)
    result.update(_convert_deploy(src.pop("deploy", {}), mem_limit))
    _transform(src, "user", result, "securityContext", _user_to_security_context)
    if src.pop("expose", None) is not None:
        # Log at info level because this does not affect the service.
        logger.info(
            f"Ignoring 'expose' key in service '{name}': all ports are open in K8s "
            "and the expose key only serves as documentation in Docker Compose."
        )
    if src.pop("init", None) is not None:
        # Warn for init because it could materially affect the service.
        logger.warning(
            f"Ignoring 'init' key in service '{name}': not supported in K8s."
        )
    # Raise an error for unsupported keys.
    if unsupported := _get_keys(src):
        raise ValueError(f"Unsupported keys {unsupported} in service '{name}'.")
    return result


def _convert_env(compose_env: dict[str, Any] | list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
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


def _convert_deploy(
    compose_deploy: dict[str, Any], mem_limit: str | None
) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    if resources := compose_deploy.pop("resources", None):
        result["resources"] = _convert_resources(resources)
        if mem_limit:
            logger.warning(
                f"Ignoring 'mem_limit: {mem_limit}' because deploy.resources is set "
                "which takes precedence."
            )
    elif mem_limit:
        result["resources"] = {"limits": {"memory": _convert_byte_value(mem_limit)}}
    if unsupported := _get_keys(compose_deploy):
        raise ValueError(f"Unsupported keys in deploy: {unsupported}")
    return result


def _convert_resources(compose_resources: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    limits = compose_resources.get("limits")
    if limits:
        result["limits"] = _convert_resource(limits)
    reservations = compose_resources.get("reservations")
    if reservations:
        result["requests"] = _convert_resource(reservations)
    return result


def _convert_resource(original: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    if cpu := original.pop("cpus", None):
        result["cpu"] = cpu
    if memory := original.pop("memory", None):
        result["memory"] = _convert_byte_value(memory)
    if original:
        raise ValueError(f"Unrecognised keys in 'resource': {original}")
    return result


def _convert_byte_value(memory: str) -> str:
    """Convert Docker compose byte values (memory quantity) to Ki/Mi/Gi.

    https://docs.docker.com/reference/compose-file/extension/#specifying-byte-values
    """

    def convert_unit(unit: str) -> str:
        match unit.lower():
            case "b":
                return ""
            case "k" | "kb":
                return "Ki"
            case "m" | "mb":
                return "Mi"
            case "g" | "gb":
                return "Gi"
            case _:
                raise ValueError(
                    f"Unrecognised byte value (memory quantity) unit: '{unit}'."
                )

    # Despite not being documented, Docker Compose allows uppercase units.
    match = re.match(r"^(?P<value>\d+)(?P<unit>gb?|mb?|kb?|b)$", memory, re.IGNORECASE)
    if not match:
        raise ValueError(f"Unrecognised byte value (memory quantity): '{memory}'.")
    return f"{match.group('value')}{convert_unit(match.group('unit'))}"


def _healthcheck_to_readiness_probe(
    src: dict[str, Any],
) -> dict[str, Any]:
    """Assume that healthchecks are to be mapped to readiness probes."""
    result: dict[str, Any] = {}
    # Allow KeyError to be raised if test is not present.
    result["exec"] = _convert_healthcheck_test_to_exec(src.pop("test"))
    _transform(
        src, "start_period", result, "initialDelaySeconds", _duration_str_to_seconds
    )
    _transform(src, "interval", result, "periodSeconds", _duration_str_to_seconds)
    _transform(src, "timeout", result, "timeoutSeconds", _duration_str_to_seconds)
    # N retries is equivalent to a failureThreshold of N+1.
    _transform(src, "retries", result, "failureThreshold", lambda x: x + 1)
    if src.pop("start_interval", None):
        logger.info("Ignoring 'start_interval' in healthcheck: not supported in K8s.")
    if unsupported := _get_keys(src):
        raise ValueError(f"Unsupported keys in healthcheck: {unsupported}")
    return result


def _convert_healthcheck_test_to_exec(test: list[str]) -> dict[str, Any]:
    if test[0] == "CMD":
        return {"command": test[1:]}
    if test[0] == "CMD-SHELL":
        return {"command": ["/bin/sh", "-c", test[1]]}
    raise ValueError(f"Unsupported healthcheck test: {test}")


def _user_to_security_context(user: str) -> dict[str, Any]:
    if isinstance(user, str) and ":" in user:
        uid, gid = user.split(":", maxsplit=1)
        return {"runAsUser": int(uid), "runAsGroup": int(gid)}
    return {"runAsUser": int(user)}


def _duration_str_to_seconds(duration: str) -> int:
    """Convert Docker Compose duration format (e.g., '30s', '1m') to seconds.

    https://docs.docker.com/reference/compose-file/extension/#specifying-durations
    """
    match = re.match(
        r"^((?P<hours>\d+)h)?((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?$", str(duration)
    )
    if not match:
        raise ValueError(
            f"Unsupported duration format: '{duration}'. Only h, m, s supported e.g. "
            "1m30s."
        )
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def _get_keys(dict: dict[str, Any], ignore: set[str] = set()) -> set[str]:
    return set(dict) - ignore


def _str_to_list(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        # Split on whitespace.
        return value.split()
    return value
