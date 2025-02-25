from pathlib import Path

import pytest
import yaml

from k8s_sandbox._compose.converter import convert_compose_to_helm_values


@pytest.fixture
def resources() -> Path:
    return Path(__file__).parent / "resources" / "compose" / "basic"


def test_converter(resources: Path) -> None:
    expected = (resources / "helm-values.yaml").read_text()

    result = convert_compose_to_helm_values(resources / "compose.yaml")
    actual = yaml.dump(result, sort_keys=False)

    assert actual == expected


def tmp_compose_file(contents: str, tmp_path: Path) -> Path:
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text(contents)
    return compose_path


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
