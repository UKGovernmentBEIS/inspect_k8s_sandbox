from pathlib import Path

import pytest
import yaml

from k8s_sandbox._compose_adapter import convert


@pytest.fixture
def resources() -> Path:
    return Path(__file__).parent / "resources" / "compose" / "basic"


def test_converter(resources: Path) -> None:
    expected = (resources / "helm-values.yaml").read_text()

    result = convert(resources / "compose.yaml")
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

    result = convert(compose_path)

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

    result = convert(compose_path)

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

    result = convert(compose_path)

    assert result["services"]["my-service"]["resources"]["limits"]["memory"] == "2Gi"
