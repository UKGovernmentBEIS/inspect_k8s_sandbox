import logging
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from k8s_sandbox.compose._converter import (
    ComposeConverterError,
    convert_compose_to_helm_values,
)

TmpComposeFixture = Callable[[str], Path]


@pytest.fixture
def resources() -> Path:
    return Path(__file__).parent / "resources" / "basic"


@pytest.fixture
def tmp_compose(tmp_path: Path) -> Callable[[str], Path]:
    def create(contents: str) -> Path:
        compose_path = tmp_path / "compose.yaml"
        compose_path.write_text(contents)
        return compose_path

    return create


def test_converter_on_real_file(resources: Path) -> None:
    expected = (resources / "helm-values.yaml").read_text()

    result = convert_compose_to_helm_values(resources / "compose.yaml")
    actual = yaml.dump(result, sort_keys=False)

    assert actual == expected


def test_converter_validates_against_compose_schema(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  42""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert (
        "The provided Docker Compose file failed validation against the Compose schema:"
        " 42 is not of type 'object'. Compose file: '/" in str(exc_info.value)
    )


### Top level elements


def test_ignores_version(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
version: "3.8"
services:
  my-service:
    image: my-image
""")

    convert_compose_to_helm_values(compose_path)


def test_ensures_services_key_exists(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
volumes:
  my-volume:
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "The 'services' key is required" in str(exc_info.value)


def test_rejects_unsupported_top_level_elements(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
    my-service:
        image: my-image
secrets: {}
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported top-level key(s)" in str(exc_info.value)


### Service elements


def test_converts_runtime(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    runtime: runc
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["runtimeClassName"] == "runc"


def test_converts_image(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["image"] == "my-image"


def test_converts_entrypoint(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    entrypoint: /bin/sh
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["command"] == ["/bin/sh"]


def test_converts_entrypoint_with_spaces(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    entrypoint: /bin/sh -c
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["command"] == ["/bin/sh", "-c"]


def test_converts_entrypoint_list(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    entrypoint:
      - /bin/sh
      - -c
      - env
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["command"] == ["/bin/sh", "-c", "env"]


def test_converts_command(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    command: foo
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["args"] == ["foo"]


def test_converts_command_with_spaces(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    command: foo bar
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["args"] == ["foo", "bar"]


def test_converts_command_list(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    command:
      - foo
      - bar
      - baz
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["args"] == ["foo", "bar", "baz"]


def test_converts_working_dir(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    working_dir: /app
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["workingDir"] == "/app"


def test_sets_dns_record_true_for_every_service(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
    default:
      image: my-image
    victim:
      image: my-image
""")

    result = convert_compose_to_helm_values(compose_path)

    assert all(service["dnsRecord"] is True for service in result["services"].values())


def test_converts_environment_dict(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    environment:
      FOO: bar
      BAZ: 42
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["env"] == [
        {"name": "FOO", "value": "bar"},
        {"name": "BAZ", "value": 42},
    ]


def test_converts_environment_list(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    environment:
      - FOO=bar
      - BAZ=42
      - MY_QUOTED_VAR="quoted value"
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["env"] == [
        {"name": "FOO", "value": "bar"},
        {"name": "BAZ", "value": "42"},
        # Quotes are retained (same as Docker Compose).
        {"name": "MY_QUOTED_VAR", "value": '"quoted value"'},
    ]


def test_rejects_invalid_environment_list(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    environment:
      - FOO
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Invalid environment variable: 'FOO'" in str(exc_info.value)


def test_converts_service_volumes(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    volumes:
      - /my-volume:/mnt/volume
      - /my_other_volume:/mnt/other_volume
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["volumes"] == [
        "/my-volume:/mnt/volume",
        # Verify that the underscores are converted to dashes.
        "/my-other-volume:/mnt/other_volume",
    ]


def test_converts_healthcheck_to_readiness_probe(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost"]
      interval: 30s
      timeout: 10s
      start_period: 40s
      start_interval: 5s
      retries: 3
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["readinessProbe"] == {
        "exec": {"command": ["curl", "-f", "http://localhost"]},
        "initialDelaySeconds": 40,
        "periodSeconds": 30,
        "timeoutSeconds": 10,
        "failureThreshold": 4,
    }


def test_converts_healthcheck_to_readiness_probe_cmd_shell(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost"]
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["readinessProbe"] == {
        "exec": {"command": ["sh", "-c", "curl -f http://localhost"]},
    }


def test_rejects_unsupported_healthcheck_test(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    healthcheck:
      test: ["INVALID", "curl -f http://localhost"]
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported 'healthcheck.test'" in str(exc_info.value)


def test_rejects_unsupported_healthcheck_key(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost"]
      disable: true
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported key(s) in 'healthcheck'" in str(exc_info.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("42s", 42),
        ("42m", 2520),
        ("42h", 151200),
        ("1h2m3s", 3723),
    ],
)
def test_can_convert_duration_str_to_seconds(
    value: str, expected: int, tmp_compose: TmpComposeFixture
) -> None:
    compose_path = tmp_compose(f"""
services:
  my-service:
    healthcheck:
      interval: {value}
      test: ["CMD", "curl", "-f", "http://localhost"]
""")

    result = convert_compose_to_helm_values(compose_path)

    actual = result["services"]["my-service"]["readinessProbe"]["periodSeconds"]
    assert actual == expected


@pytest.mark.parametrize("value", ["1x", "1d", "1us", "1ns", "1s2m3h"])
def test_rejects_unsupported_durations(
    value: str, tmp_compose: TmpComposeFixture
) -> None:
    compose_path = tmp_compose(f"""
services:
  my-service:
    healthcheck:
      interval: {value}
      test: ["CMD", "curl", "-f", "http://localhost"]
 """)

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported duration format" in str(exc_info.value)


def test_converts_mem_limit(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    mem_limit: 1G
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"]["limits"]["memory"] == "1Gi"


def test_converts_fractional_mem_limit(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    mem_limit: 0.5G
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"]["limits"]["memory"] == "0.5Gi"


def test_converts_mem_limit_and_applies_to_requests(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    mem_limit: 1G
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"] == {
        "limits": {"memory": "1Gi"},
        "requests": {"memory": "1Gi"},
    }


def test_converts_deploy(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: 0.5
        reservations:
            memory: 512M
            cpus: 0.25
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"] == {
        "limits": {"memory": "1Gi", "cpu": 0.5},
        "requests": {"memory": "512Mi", "cpu": 0.25},
    }


def test_applies_limits_to_requests_if_unset(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: 0.5
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"] == {
        "limits": {"memory": "1Gi", "cpu": 0.5},
        "requests": {"memory": "1Gi", "cpu": 0.5},
    }


def test_ignores_mem_limit_when_deploy_present(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    mem_limit: 1G
    deploy:
      resources:
        limits:
          memory: 2G
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"]["limits"]["memory"] == "2Gi"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("512b", "512"),
        ("512B", "512"),
        ("1k", "1Ki"),
        ("1kb", "1Ki"),
        ("1K", "1Ki"),
        ("1KB", "1Ki"),
        ("2m", "2Mi"),
        ("2mb", "2Mi"),
        ("2M", "2Mi"),
        ("2MB", "2Mi"),
        ("3g", "3Gi"),
        ("3gb", "3Gi"),
        ("3G", "3Gi"),
        ("3GB", "3Gi"),
    ],
)
def test_can_convert_byte_value(
    value: str, expected: str, tmp_compose: TmpComposeFixture
) -> None:
    compose_path = tmp_compose(f"""
services:
  my-service:
    mem_limit: {value}
""")

    result = convert_compose_to_helm_values(compose_path)

    actual = result["services"]["my-service"]["resources"]["limits"]["memory"]
    assert actual == expected


def test_rejects_unsupported_byte_values(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    mem_limit: 1x
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported byte value (memory quantity)" in str(exc_info.value)


def test_rejects_unsupported_deploy_key(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    deploy:
      endpoint_mode: vip
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported key(s) in 'deploy'" in str(exc_info.value)


def test_rejects_unsupported_resources_key(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    deploy:
      resources:
        x-unsupported: 42
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported key(s) in 'resources'" in str(exc_info.value)


def test_converts_user_to_security_context(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    user: "1000"
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["securityContext"]["runAsUser"] == 1000


def test_converts_user_with_gid_to_security_context(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    user: 1000:1001
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["securityContext"]["runAsUser"] == 1000
    assert result["services"]["my-service"]["securityContext"]["runAsGroup"] == 1001


def test_rejects_invalid_user(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    user: foo
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Invalid 'user' value" in str(exc_info.value)


def test_rejects_invalid_user_gid(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    user: "1000:foo"
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Invalid 'user' value" in str(exc_info.value)


def test_ignores_init_true(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    init: true
""")

    result = convert_compose_to_helm_values(compose_path)

    assert "my-service" in result["services"]


def test_ignores_expose_key(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    expose:
      - 80
""")

    result = convert_compose_to_helm_values(compose_path)

    assert "my-service" in result["services"]


@pytest.mark.parametrize(
    ("properties", "expected_warning"),
    [
        ({"x-local": "true"}, "`x-local: true`"),
        ({"build": {"context": "."}}, "`build`"),
        ({"x-local": "true", "build": {"context": "."}}, "`x-local: true` and `build`"),
    ],
)
def test_ignores_and_warns_build_keys(
    caplog: pytest.LogCaptureFixture,
    tmp_compose: TmpComposeFixture,
    properties: dict[str, Any],
    expected_warning: str,
) -> None:
    compose_path = tmp_compose(
        yaml.safe_dump(
            {
                "services": {
                    "my-service": {
                        "image": "my-image",
                        **properties,
                    },
                    "my-service-2": {
                        "image": "my-image",
                        **properties,
                    },
                }
            }
        )
    )

    with caplog.at_level(logging.WARNING):
        result = convert_compose_to_helm_values(compose_path)

    expected_ignored = {*properties}
    for service_name in "my-service", "my-service-2":
        assert service_name in result["services"]
        bad_keys = expected_ignored.intersection(result["services"][service_name])
        assert len(bad_keys) == 0

    assert len(caplog.records) == len(result["services"])
    for record in caplog.records:
        assert expected_warning in record.message


def test_rejects_unsupported_service_key(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    network_mode: none
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    # Verify that the error message includes the service name, invalid keys and the
    # compose file path.
    assert (
        "Unsupported key(s) in 'service': {'network_mode'}. Service: 'my-service'; "
        "Compose file: '/" in str(exc_info.value)
    )


def test_converts_hostname_if_identical_to_service_name(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    hostname: my-service
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["dnsRecord"]


def test_rejects_hostname_if_not_identical_to_service_name(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    hostname: other-hostname
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported hostname" in str(exc_info.value)


### Volume elements


def test_converts_top_level_volumes(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
volumes:
  my-volume:
  my_other_volume:
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["volumes"]["my-volume"] is not None
    assert result["volumes"]["my-other-volume"] is not None


def test_rejects_non_empty_top_level_volumes(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
volumes:
  my-volume:
    driver: local
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "non-empty volume values is not supported" in str(exc_info.value)


### Extension elements


def test_converts_allow_domains(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
x-inspect_k8s_sandbox:
  allow_domains:
    - example.com
    - example.org
""")

    result = convert_compose_to_helm_values(compose_path)

    assert result["allowDomains"] == ["example.com", "example.org"]


def test_rejects_invalid_allow_domains(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
x-inspect_k8s_sandbox:
  allow_domains: "invalid"
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Invalid 'allow_domains' type" in str(exc_info.value)


def test_rejects_unsupported_extension_key(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
x-inspect_k8s_sandbox:
  invalid: "invalid"
""")

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported key(s) in 'x-inspect_k8s_sandbox'" in str(exc_info.value)


### Network elements


def test_converts_networks(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    networks:
        - my-network
networks:
    my-network:
        driver: bridge
        internal: true
    """)

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["networks"] == ["my-network"]
    assert result["networks"]["my-network"] is not None
    assert result["networks"]["my-network"] == {}


def test_rejects_unsupported_network_driver(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    networks:
        - my-network
networks:
    my-network:
        driver: host
        internal: true
    """)

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported network driver" in str(exc_info.value)


def test_rejects_non_internal_network(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  my-service:
    image: my-image
    networks:
        - my-network
networks:
    my-network:
        driver: bridge
    """)

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported network internal value" in str(exc_info.value)


### x-default handling


def test_renames_service_with_x_default_to_default(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  calculator_improvement:
    image: my-image
    x-default: true
""")

    result = convert_compose_to_helm_values(compose_path)

    # Verify that service name changed to "default"
    assert "default" in result["services"]
    assert "calculator_improvement" not in result["services"]
    assert result["services"]["default"]["image"] == "my-image"

    # Ensure x-default is not in the final output
    assert "x-default" not in result["services"]["default"]


def test_keeps_service_named_default_unchanged(tmp_compose: TmpComposeFixture) -> None:
    compose_path = tmp_compose("""
services:
  default:
    image: my-image
""")

    result = convert_compose_to_helm_values(compose_path)

    assert "default" in result["services"]
    assert result["services"]["default"]["image"] == "my-image"


def test_service_named_default_takes_precedence_over_x_default(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  default:
    image: default-image
  other-service:
    image: other-image
    x-default: true
""")

    result = convert_compose_to_helm_values(compose_path)

    # The service named "default" should remain, not be overwritten
    assert result["services"]["default"]["image"] == "default-image"
    assert "other-service" in result["services"]


def test_no_renaming_when_no_default_or_x_default(
    tmp_compose: TmpComposeFixture,
) -> None:
    compose_path = tmp_compose("""
services:
  first-service:
    image: first-image
  second-service:
    image: second-image
""")

    result = convert_compose_to_helm_values(compose_path)

    # Services should keep their original names when no x-default is set
    assert "first-service" in result["services"]
    assert result["services"]["first-service"]["image"] == "first-image"
    assert "second-service" in result["services"]
    assert "default" not in result["services"]
