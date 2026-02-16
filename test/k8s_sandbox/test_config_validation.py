from pathlib import Path

import pytest
from inspect_ai.util._sandbox.compose import ComposeConfig
from pydantic import BaseModel

from k8s_sandbox import K8sSandboxEnvironment, K8sSandboxEnvironmentConfig
from k8s_sandbox._sandbox_environment import _validate_and_resolve_k8s_sandbox_config

VALID_VALUES = str(Path(__file__).parent / "resources" / "values.yaml")


async def test_invalid_values_path_as_str() -> None:
    with pytest.raises(FileNotFoundError):
        await K8sSandboxEnvironment.sample_init(__file__, "fake.yaml", {})


async def test_invalid_values_path() -> None:
    with pytest.raises(FileNotFoundError):
        await K8sSandboxEnvironment.sample_init(
            __file__, K8sSandboxEnvironmentConfig(values=Path("fake.yaml")), {}
        )


async def test_invalid_chart() -> None:
    with pytest.raises(NotADirectoryError):
        await K8sSandboxEnvironment.sample_init(
            __file__, K8sSandboxEnvironmentConfig(chart="chart-does-not-exist"), {}
        )


async def test_invalid_kubeconfig_context_name() -> None:
    with pytest.raises(ValueError):
        await K8sSandboxEnvironment.sample_init(
            __file__, K8sSandboxEnvironmentConfig(context="invalid-context"), {}
        )


async def test_invalid_config_type() -> None:
    class MyModel(BaseModel, frozen=True):
        pass

    with pytest.raises(TypeError):
        await K8sSandboxEnvironment.sample_init(__file__, MyModel(), {})


def test_can_serialize_and_deserialize_config() -> None:
    original = K8sSandboxEnvironmentConfig(
        chart="my-chart", values=Path("my-values.yaml"), context="my-context"
    )

    as_json = original.model_dump()
    recreated = K8sSandboxEnvironmentConfig.model_validate(as_json)

    assert recreated == original


def test_is_docker_compatible() -> None:
    assert K8sSandboxEnvironment.is_docker_compatible() is True


def test_config_deserialize_k8s_config() -> None:
    result = K8sSandboxEnvironment.config_deserialize(
        {"chart": "my-chart", "values": "my-values.yaml"}
    )
    assert isinstance(result, K8sSandboxEnvironmentConfig)
    assert result.chart == "my-chart"


def test_config_deserialize_compose_config() -> None:
    result = K8sSandboxEnvironment.config_deserialize(
        {"services": {"default": {"image": "python:3.11"}}}
    )
    assert isinstance(result, ComposeConfig)
    assert "default" in result.services


def test_config_deserialize_empty_dict() -> None:
    result = K8sSandboxEnvironment.config_deserialize({})
    assert isinstance(result, K8sSandboxEnvironmentConfig)


def test_validate_compose_config() -> None:
    compose_config = ComposeConfig.model_validate(
        {"services": {"default": {"image": "python:3.11"}}}
    )
    resolved = _validate_and_resolve_k8s_sandbox_config(compose_config)
    assert resolved.chart is None
    assert resolved.values is None
    assert resolved.compose_config is compose_config
