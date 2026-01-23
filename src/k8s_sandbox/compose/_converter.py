import json
import logging
import re
from functools import cached_property
from pathlib import Path
from typing import Any, Callable

import jsonschema
import yaml

logger = logging.getLogger(__name__)

COMPOSE_SCHEMA_PATH = (
    Path(__file__).parent.parent / "resources" / "compose" / "compose-spec.json"
)


class ComposeConverterError(Exception):
    """Raised when an error occurs converting a Docker Compose file to Helm values."""

    pass


def convert_compose_to_helm_values(compose_file: Path) -> dict[str, Any]:
    """Convert a Docker Compose file to Helm values.

    The resulting Helm values file is suitable for the built-in Helm chart.

    Please see the docs site at /helm/compose-to-helm for more information and
    rationale.

    Implementation notes:
    Using Pydantic models was considered. To leverage data validation, the models cannot
    be built incrementally (unless all fields are made optional). Therefore, a dict (or
    similar) approach is required anyway. Additionally, the built-in Helm chart has
    a schema which is the source of truth and automatically validated against upon
    install.

    Returns:
        A dictionary representing the generated Helm values.
    """
    compose = yaml.safe_load(compose_file.read_text())
    _validate_compose(compose, compose_file)
    result: dict[str, Any] = dict()
    services = compose.pop("services", None)
    if services is None:
        raise ComposeConverterError(
            f"The 'services' key is required. Compose file: '{compose_file}'."
        )
    result["services"] = _convert_services(services, compose_file)
    if volumes := compose.pop("volumes", None):
        result["volumes"] = _convert_volumes(volumes, compose_file)
    if networks := compose.pop("networks", None):
        result["networks"] = _convert_networks(networks, compose_file)
    # The 'x-inspect_k8s_sandbox' key is used to add additional configuration to
    # Docker Compose files for use in Helm values.
    if extensions := compose.pop("x-inspect_k8s_sandbox", None):
        result.update(_convert_extensions(extensions, compose_file))
    # Ignore the version key.
    compose.pop("version", None)
    if compose:
        raise ComposeConverterError(
            f"Unsupported top-level key(s) in Docker Compose file: {set(compose)}. "
            f"Compose file: '{compose_file}'."
        )
    return result


def _validate_compose(compose: dict[str, Any], compose_file: Path) -> None:
    schema = json.loads(COMPOSE_SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(compose, schema)
    except jsonschema.ValidationError as e:
        raise ComposeConverterError(
            f"The provided Docker Compose file failed validation against the Compose "
            f"schema: {e.message}. Compose file: '{compose_file}'."
        )


def _convert_services(src: dict[str, Any], compose_file: Path) -> dict[str, Any]:
    result: dict[str, Any] = dict()

    service_to_rename = _determine_default_service(src)

    for service_name, service_value in src.items():
        service_converter = _ServiceConverter(service_name, service_value, compose_file)
        # Rename the service to "default" if needed
        output_service_name = (
            "default" if service_name == service_to_rename else service_name
        )
        result[output_service_name] = service_converter.convert()
    return result


def _determine_default_service(services: dict[str, Any]) -> str | None:
    """Determine which service should be renamed to "default".

    Returns the service name that should be renamed to "default", or None if no
    renaming is needed.

    Priority:
    1. Service named "default" - no renaming needed (returns None)
    2. Service with x-default: true - should be renamed to "default"
    3. First service (only if multiple services) - rename to "default"

    See: https://inspect.aisi.org.uk/sandboxing.html#multiple-environments
    """
    if "default" in services:
        return None

    for service_name, service_value in services.items():
        if isinstance(service_value, dict) and service_value.get("x-default") is True:
            return service_name

    if len(services) > 1:
        return next(iter(services.keys()), None)

    return None


def _convert_volumes(src: dict[str, Any], compose_file: Path) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    for volume_name, volume_value in src.items():
        if volume_value:
            raise ComposeConverterError(
                f"Unsupported volume value: '{volume_value}'. Converting non-empty "
                f"volume values is not supported. Compose file: '{compose_file}'."
            )
        result[_make_volume_name_k8s_compliant(volume_name)] = {}
    return result


def _convert_networks(src: dict[str, Any], compose_file: Path) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    for network_name, network_value in src.items():
        if not isinstance(network_value, dict):
            raise ComposeConverterError(
                f"Invalid network value: '{network_value}'. Expected dict. "
                f"Compose file: '{compose_file}'."
            )
        driver = network_value.pop("driver", None)
        if driver != "bridge":
            raise ComposeConverterError(
                f"Unsupported network driver: '{driver}'. Only 'bridge' is "
                f"supported. Compose file: '{compose_file}'."
            )
        internal = network_value.pop("internal", False)
        if internal is not True:
            raise ComposeConverterError(
                f"Unsupported network internal value: '{internal}'. Only "
                f"'internal: true' is supported. Compose file: '{compose_file}'."
            )
        if network_value:
            raise ComposeConverterError(
                f"Unsupported key(s) in network '{network_name}': "
                f"{set(network_value)}. Compose file: '{compose_file}'."
            )
        result[network_name] = {}
    return result


def _convert_extensions(
    extensions: dict[str, Any], compose_file: Path
) -> dict[str, Any]:
    result: dict[str, Any] = dict()
    if allow_domains := extensions.pop("allow_domains", None):
        if not isinstance(allow_domains, list):
            raise ComposeConverterError(
                f"Invalid 'allow_domains' type: {type(allow_domains)}. Expected list. "
                f"Compose file: '{compose_file}'."
            )
        result["allowDomains"] = allow_domains
    if allow_entities := extensions.pop("allow_entities", None):
        if not isinstance(allow_entities, list):
            raise ComposeConverterError(
                f"Invalid 'allow_entities' type: {type(allow_entities)}. Expected list. "
                f"Compose file: '{compose_file}'."
            )
        result["allowEntities"] = allow_entities
    if extensions:
        raise ComposeConverterError(
            f"Unsupported key(s) in 'x-inspect_k8s_sandbox': {set(extensions)}. "
            f"Compose file: '{compose_file}'."
        )
    return result


class _ServiceConverter:
    """
    Converts a Docker Compose service to a service for the built-in Helm chart.

    Implemented as a class to facilitate flowing context information to error and
    logging messages such as the service name and originating Compose file.

    The src_service dict will be mutated during conversion.
    """

    def __init__(self, name: str, src_service: dict[str, Any], compose_file: Path):
        self._name = name
        self._src_service = src_service
        self._compose_file = compose_file

    def convert(self) -> dict[str, Any]:
        return self._convert_service(self._src_service)

    @cached_property
    def context(self):
        # A reference to the service being converted for logging & error messages.
        return f"Service: '{self._name}'; Compose file: '{self._compose_file}'."

    def _convert_service(self, src: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = dict()
        # Ordered as per built-in Helm chart values.yaml documentation.
        _transform(src, "runtime", result, "runtimeClassName")
        _transform(src, "image", result, "image")
        _transform(src, "entrypoint", result, "command", _str_to_list)
        _transform(src, "command", result, "args", _str_to_list)
        _transform(src, "working_dir", result, "workingDir")
        # Create a DNS record for every service (same behaviour as Docker Compose).
        result["dnsRecord"] = True
        _transform(src, "environment", result, "env", self._convert_env)
        _transform(src, "volumes", result, "volumes", self._convert_volumes)
        _transform(
            src,
            "healthcheck",
            result,
            "readinessProbe",
            self._healthcheck_to_readiness_probe,
        )
        # Memory limits can be specified in either deploy.resources or mem_limit.
        mem_limit = src.pop("mem_limit", None)
        result.update(self._convert_deploy(src.pop("deploy", {}), mem_limit))
        _transform(
            src, "user", result, "securityContext", self._user_to_security_context
        )
        # Check for network_mode first
        network_mode = src.pop("network_mode", None)
        networks = src.get("networks")

        if network_mode == "none":
            if networks is not None:
                raise ComposeConverterError(
                    f"Cannot specify both 'network_mode: none' and 'networks'. "
                    f"{self.context}"
                )
            result["networkIsolated"] = True
        elif network_mode is not None:
            raise ComposeConverterError(
                f"Unsupported network_mode: '{network_mode}'. Only 'none' is "
                f"supported. {self.context}"
            )
        else:
            # Only process networks if network_mode is not set
            _transform(src, "networks", result, "networks")

        if hostname := src.pop("hostname", None):
            if hostname != self._name:
                raise ComposeConverterError(
                    f"Unsupported hostname: '{hostname}'. Only the service name is "
                    f"supported. {self.context}"
                )
        if src.pop("expose", None) is not None:
            # Log at info level because this does not affect the service.
            logger.info(
                "Ignoring 'expose' key: all ports are open in K8s; and the expose key "
                f"only serves as documentation in Docker Compose. {self.context}"
            )
        if src.pop("init", None) is not None:
            # The fact that `init: true` is unsupported could materially affect the
            # service, so may be worthy of a warning, but on the other hand, this
            # is almost always used in Compose and we don't have an alternative
            # suggestion to offer to users.
            logger.info(f"Ignoring 'init' key: not supported in K8s. {self.context}")

        has_x_local = src.pop("x-local", None) == "true"
        has_build = src.pop("build", None) is not None
        src.pop(
            "x-default", None
        )  # Remove x-default key (handled during service detection)
        # x-local is an Inspect-specific key to indicate that an image should not be
        # pulled. https://inspect.aisi.org.uk/sandboxing.html#task-configuration
        # If it is set to anything but "true", silently ignore it.
        if has_x_local or has_build:
            ignored: list[str] = []
            if has_x_local:
                ignored.append("`x-local: true`")
            if has_build:
                ignored.append("`build`")
            logger.warning(
                f"Ignoring {' and '.join(ignored)}: not supported in K8s. All images "
                f"must be available for pulling from a container registry. "
                f"{self.context}"
            )
        if src:
            raise ComposeConverterError(
                f"Unsupported key(s) in 'service': {set(src)}. {self.context}"
            )
        return result

    def _convert_env(self, src: dict[str, Any] | list[str]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if isinstance(src, dict):
            for key, value in src.items():
                result.append({"name": key, "value": value})
        elif isinstance(src, list):
            for item in src:
                if "=" not in item:
                    raise ComposeConverterError(
                        f"Invalid environment variable: '{item}'. Expected list items "
                        f"to contain '='. {self.context}"
                    )
                key, value = item.split("=", maxsplit=1)
                # Note: Just like with Docker Compose, quoted values e.g. `key="value"`
                # should retain the quotes (meaning quote marks are not stripped).
                result.append({"name": key, "value": value})
        else:
            raise ComposeConverterError(
                f"Invalid 'environment' format. Expected dict or list but got "
                f"{type(src)}. {self.context}"
            )
        return result

    def _convert_volumes(self, src: list[str]) -> list[str]:
        result: list[str] = []
        if not isinstance(src, list):
            raise ComposeConverterError(
                f"Invalid 'volumes' type: {type(src)}. Expected list. {self.context}"
            )
        for item in src:
            if ":" not in item:
                raise ComposeConverterError(
                    f"Invalid service volume: '{item}'. Expected list items to contain "
                    f"':'. {self.context}"
                )
            volume_name, mount_path = item.split(":", maxsplit=1)
            result.append(
                f"{_make_volume_name_k8s_compliant(volume_name)}:{mount_path}"
            )
        return result

    def _healthcheck_to_readiness_probe(self, src: dict[str, Any]) -> dict[str, Any]:
        """Assume that healthchecks are to be mapped to readiness probes."""
        result: dict[str, Any] = {}
        # Allow KeyError to be raised if test is not present.
        result["exec"] = self._convert_healthcheck_test_to_exec(src.pop("test"))
        _transform(
            src,
            "start_period",
            result,
            "initialDelaySeconds",
            self._duration_to_seconds,
        )
        _transform(src, "interval", result, "periodSeconds", self._duration_to_seconds)
        _transform(src, "timeout", result, "timeoutSeconds", self._duration_to_seconds)
        # N retries is equivalent to a failureThreshold of N+1.
        _transform(src, "retries", result, "failureThreshold", lambda x: x + 1)
        if src.pop("start_interval", None):
            logger.info(
                f"Ignoring 'start_interval' in 'healthcheck': not supported in K8s. "
                f"{self.context}"
            )
        if src:
            raise ComposeConverterError(
                f"Unsupported key(s) in 'healthcheck': {set(src)}. {self.context}"
            )
        return result

    def _convert_healthcheck_test_to_exec(self, test: list[str]) -> dict[str, Any]:
        if test[0] == "CMD":
            return {"command": test[1:]}
        if test[0] == "CMD-SHELL":
            return {"command": ["sh", "-c", test[1]]}
        raise ComposeConverterError(
            f"Unsupported 'healthcheck.test': '{test}'. Only CMD and CMD-SHELL "
            f"are supported. {self.context}"
        )

    def _convert_deploy(
        self, src: dict[str, Any], mem_limit: str | None
    ) -> dict[str, Any]:
        result: dict[str, Any] = dict()
        if resources := src.pop("resources", None):
            result["resources"] = self._convert_resources(resources)
            self._set_requests_to_limits_if_unset(result["resources"])
            if mem_limit:
                # Log at warning because this is likely a mistake.
                logger.warning(
                    f"Ignoring 'mem_limit: {mem_limit}' because deploy.resources is "
                    f"set which takes precedence. {self.context}"
                )
        elif mem_limit:
            result["resources"] = {
                "limits": {"memory": self._convert_byte_value(mem_limit)}
            }
            self._set_requests_to_limits_if_unset(result["resources"])
        if src:
            raise ComposeConverterError(
                f"Unsupported key(s) in 'deploy': {set(src)}. {self.context}"
            )
        return result

    def _convert_resources(self, src: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = dict()
        if limits := src.pop("limits", None):
            result["limits"] = self._convert_resource(limits)
        if reservations := src.pop("reservations", None):
            result["requests"] = self._convert_resource(reservations)
        if src:
            raise ComposeConverterError(
                f"Unsupported key(s) in 'resources': {set(src)}. {self.context}"
            )
        return result

    def _convert_resource(self, src: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = dict()
        if cpu := src.pop("cpus", None):
            # Kubernetes supports fractional CPU values like Docker Compose does.
            result["cpu"] = cpu
        if memory := src.pop("memory", None):
            result["memory"] = self._convert_byte_value(memory)
        if src:
            raise ComposeConverterError(
                f"Unsupported key(s) in 'resource': {set(src)}. {self.context}"
            )
        return result

    def _convert_byte_value(self, value: str) -> str:
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
                    raise ComposeConverterError(
                        f"Unsupported byte value (memory quantity) unit: '{unit}'. "
                        f"{self.context}"
                    )

        # Despite not being documented, Docker Compose allows uppercase units.
        match = re.match(
            r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>gb?|mb?|kb?|b)$", value, re.IGNORECASE
        )
        if not match:
            raise ComposeConverterError(
                f"Unsupported byte value (memory quantity): '{value}'. {self.context}"
            )
        return f"{match.group('value')}{convert_unit(match.group('unit'))}"

    def _set_requests_to_limits_if_unset(self, resources: dict[str, Any]) -> None:
        # As per the built-in Helm chart, set limits == requests for improved QoS.
        if "limits" in resources and "requests" not in resources:
            # Copy to avoid references which can result in anchors and aliases in YAML.
            resources["requests"] = resources["limits"].copy()

    def _user_to_security_context(self, user: str | int) -> dict[str, Any]:
        def parse_int(value: str) -> int:
            try:
                return int(value)
            except ValueError:
                raise ComposeConverterError(
                    f"Invalid 'user' value: '{value}'. Expected int. {self.context}"
                )

        if isinstance(user, int):
            return {"runAsUser": user}
        if isinstance(user, str):
            if ":" in user:
                uid, gid = user.split(":", maxsplit=1)
                return {"runAsUser": parse_int(uid), "runAsGroup": parse_int(gid)}
            return {"runAsUser": parse_int(user)}
        raise ComposeConverterError(
            f"Invalid 'user' type: {type(user)} with value '{user}'. Expected int or "
            f"str. {self.context}"
        )

    def _duration_to_seconds(self, value: str) -> int:
        """Convert Docker Compose duration format (e.g., '30s', '1m') to seconds.

        https://docs.docker.com/reference/compose-file/extension/#specifying-durations
        """
        match = re.match(
            r"^((?P<hours>\d+)h)?((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?$", str(value)
        )
        if not match:
            raise ComposeConverterError(
                f"Unsupported duration format: '{value}'. Only {{h, m, s}} supported "
                f"e.g. 1m30s. {self.context}"
            )
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        seconds = int(match.group("seconds") or 0)
        return hours * 3600 + minutes * 60 + seconds


def _make_volume_name_k8s_compliant(value: str) -> str:
    # This is not exhaustive but covers common cases.
    return value.replace("_", "-").replace(".", "-").lower()


def _transform(
    src: dict[str, Any],
    src_key: str,
    dst: dict[str, Any],
    dst_key: str,
    # Default is identity function.
    fn: Callable = lambda x: x,
) -> None:
    """
    Moves a key-value pair from src to dst, applying a function to the value.

    If the key exists in src, it is removed from src and added to dst with the
    transformed value. If the key does not exist in src, nothing happens.
    """
    value = src.pop(src_key, None)
    if value is not None:
        dst[dst_key] = fn(value)


def _str_to_list(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        # Split on whitespace.
        return value.split()
    return value
