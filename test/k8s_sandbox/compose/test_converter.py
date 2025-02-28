from pathlib import Path

import pytest
import yaml

from k8s_sandbox._compose.converter import (
    ComposeConverterError,
    convert_compose_to_helm_values,
)


@pytest.fixture
def resources() -> Path:
    return Path(__file__).parent / "resources" / "basic"


def tmp_compose_file(contents: str, tmp_path: Path) -> Path:
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text(contents)
    return compose_path


def test_converter_on_real_file(resources: Path) -> None:
    expected = (resources / "helm-values.yaml").read_text()

    result = convert_compose_to_helm_values(resources / "compose.yaml")
    actual = yaml.dump(result, sort_keys=False)

    assert actual == expected


def test_ignores_version(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
version: "3.8"
services:
  my-service:
    image: my-image
""",
        tmp_path,
    )

    convert_compose_to_helm_values(compose_path)


def test_converts_entrypoint(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    entrypoint: /bin/sh
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["command"] == ["/bin/sh"]


def test_converts_entrypoint_with_spaces(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    entrypoint: /bin/sh -c
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["command"] == ["/bin/sh", "-c"]


def test_converts_entrypoint_list(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    entrypoint:
      - /bin/sh
      - -c
      - env
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["command"] == ["/bin/sh", "-c", "env"]


def test_converts_command(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    command: foo
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["args"] == ["foo"]


def test_converts_command_with_spaces(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    command: foo bar
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["args"] == ["foo", "bar"]


def test_converts_command_list(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    command:
      - foo
      - bar
      - baz
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["args"] == ["foo", "bar", "baz"]


def test_converts_working_dir(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    working_dir: /app
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["workingDir"] == "/app"


def test_sets_dns_record_true_for_every_service(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
    default:
      image: my-image
    victim:
      image: my-image
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert all(service["dnsRecord"] is True for service in result["services"].values())


def test_converts_environment_dict(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    environment:
      FOO: bar
      BAZ: 42
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["env"] == [
        {"name": "FOO", "value": "bar"},
        {"name": "BAZ", "value": 42},
    ]


def test_converts_environment_list(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    environment:
      - FOO=bar
      - BAZ=42
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["env"] == [
        {"name": "FOO", "value": "bar"},
        {"name": "BAZ", "value": "42"},
    ]


def test_rejects_invalid_environment_list(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    environment:
      - FOO
""",
        tmp_path,
    )

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Invalid environment variable: 'FOO'" in str(exc_info.value)


def test_rejects_invalid_environment_type(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    environment:
      42
""",
        tmp_path,
    )

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Invalid 'environment' format" in str(exc_info.value)


def test_converts_volumes(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    volumes:
      - /my-volume:/mnt/volume
volumes:
  my-volume:
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["volumes"] == ["/my-volume:/mnt/volume"]
    assert result["volumes"]["my-volume"] is None


def test_rejects_non_empty_volumes(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    image: my-image
volumes:
  my-volume:
    driver: local
""",
        tmp_path,
    )

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "non-empty volume values is not supported" in str(exc_info.value)


def test_converts_healthcheck_to_readiness_probe(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost"]
      interval: 30s
      timeout: 10s
      start_period: 40s
      start_interval: 5s
      retries: 3
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["readinessProbe"] == {
        "exec": {"command": ["curl", "-f", "http://localhost"]},
        "initialDelaySeconds": 40,
        "periodSeconds": 30,
        "timeoutSeconds": 10,
        "failureThreshold": 4,
    }


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
    value: str, expected: int, tmp_path: Path
) -> None:
    compose_path = tmp_compose_file(
        f"""
services:
  my-service:
    healthcheck:
      interval: {value}
      test: ["CMD", "curl", "-f", "http://localhost"]
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    actual = result["services"]["my-service"]["readinessProbe"]["periodSeconds"]
    assert actual == expected


@pytest.mark.parametrize("value", ["1", "1x", "1d", "1us", "1ns", "1s2m3h"])
def test_rejects_invalid_durations(value: str, tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        f"""
services:
  my-service:
    healthcheck:
      interval: {value}
      test: ["CMD", "curl", "-f", "http://localhost"]
""",
        tmp_path,
    )

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unsupported duration format" in str(exc_info.value)


def test_converts_mem_limit(tmp_path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    mem_limit: 1G
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"]["limits"]["memory"] == "1Gi"


def test_converts_deploy(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    deploy:
      resources:
        limits:
          memory: 1G
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["services"]["my-service"]["resources"]["limits"]["memory"] == "1Gi"


def test_ignores_mem_limit_when_deploy_present(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    mem_limit: 1G
    deploy:
      resources:
        limits:
          memory: 2G
    """,
        tmp_path,
    )

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
def test_can_convert_byte_value(value: str, expected: str, tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        f"""
services:
  my-service:
    mem_limit: {value}
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    actual = result["services"]["my-service"]["resources"]["limits"]["memory"]
    assert actual == expected


def test_rejects_invalid_byte_values(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    mem_limit: 1x
""",
        tmp_path,
    )

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "Unrecognised byte value (memory quantity)" in str(exc_info.value)


def test_ensures_services_key_exists(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
volumes:
  my-volume:
""",
        tmp_path,
    )

    with pytest.raises(ComposeConverterError) as exc_info:
        convert_compose_to_helm_values(compose_path)

    assert "The 'services' key is required" in str(exc_info.value)


def test_converts_allow_domains(tmp_path: Path) -> None:
    compose_path = tmp_compose_file(
        """
services:
  my-service:
    image: my-image
x-inspect_k8s_sandbox:
  allow_domains:
    - example.com
    - example.org
""",
        tmp_path,
    )

    result = convert_compose_to_helm_values(compose_path)

    assert result["allowDomains"] == ["example.com", "example.org"]
