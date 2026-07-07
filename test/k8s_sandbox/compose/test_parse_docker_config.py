from pathlib import Path
from typing import Callable

import pytest
import yaml
from inspect_ai.util import ComposeBuild, ComposeConfig

from k8s_sandbox.compose._compose import (
    ComposeConfigValuesSource,
    parse_docker_config,
)

TmpFileFixture = Callable[[str, str], Path]


@pytest.fixture
def tmp_file(tmp_path: Path) -> Callable[[str, str], Path]:
    def create(filename: str, contents: str = "") -> Path:
        file_path = tmp_path / filename
        file_path.write_text(contents)
        return file_path

    return create


def test_parse_dockerfile(tmp_file: TmpFileFixture) -> None:
    dockerfile = tmp_file("Dockerfile", "FROM python:3.11\nRUN echo hello")

    result = parse_docker_config(str(dockerfile))

    assert isinstance(result, ComposeConfig)
    assert "default" in result.services
    service = result.services["default"]
    assert isinstance(service.build, ComposeBuild)
    assert service.build.dockerfile == "Dockerfile"
    assert service.build.context == str(dockerfile.parent)


def test_parse_named_dockerfile(tmp_file: TmpFileFixture) -> None:
    dockerfile = tmp_file("agent.Dockerfile", "FROM ubuntu:22.04")

    result = parse_docker_config(str(dockerfile))

    assert isinstance(result, ComposeConfig)
    assert "default" in result.services
    service = result.services["default"]
    assert isinstance(service.build, ComposeBuild)
    assert service.build.dockerfile == "agent.Dockerfile"


def test_parse_compose_yaml(tmp_file: TmpFileFixture) -> None:
    compose_file = tmp_file(
        "compose.yaml",
        "services:\n  default:\n    image: python:3.11\n",
    )

    result = parse_docker_config(str(compose_file))

    assert isinstance(result, ComposeConfig)
    assert "default" in result.services
    assert result.services["default"].image == "python:3.11"


def test_parse_docker_compose_yaml(tmp_file: TmpFileFixture) -> None:
    compose_file = tmp_file(
        "docker-compose.yaml",
        "services:\n  web:\n    image: nginx:latest\n",
    )

    result = parse_docker_config(str(compose_file))

    assert isinstance(result, ComposeConfig)
    assert "web" in result.services


def test_file_not_found() -> None:
    with pytest.raises(FileNotFoundError, match="Docker config file not found"):
        parse_docker_config("/nonexistent/Dockerfile")


def test_unsupported_file_type(tmp_file: TmpFileFixture) -> None:
    values_file = tmp_file("values.yaml", "key: value\n")

    with pytest.raises(ValueError, match="neither a Dockerfile nor a Docker Compose"):
        parse_docker_config(str(values_file))


def test_security_opt_reaches_converter_via_compose_entrypoint(
    tmp_file: TmpFileFixture,
) -> None:
    # The ("k8s", "compose.yaml") entrypoint parses through inspect_ai's ComposeConfig
    # before the converter runs. Guards against that model silently rejecting
    # security_opt/memswap_limit (and so never reaching the converter).
    compose_file = tmp_file(
        "compose.yaml",
        "services:\n"
        "  default:\n"
        "    image: ubuntu:24.04\n"
        "    memswap_limit: 512m\n"
        "    security_opt:\n"
        "      - seccomp=unconfined\n",
    )

    config = parse_docker_config(str(compose_file))
    with ComposeConfigValuesSource(config).values_file() as values_file:
        assert values_file is not None
        values = yaml.safe_load(values_file.read_text())

    security_context = values["services"]["default"]["securityContext"]
    assert security_context["seccompProfile"] == {"type": "Unconfined"}
