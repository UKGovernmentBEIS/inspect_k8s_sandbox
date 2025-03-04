from pathlib import Path

import pytest
from pydantic import BaseModel

from k8s_sandbox import K8sSandboxEnvironment, K8sSandboxEnvironmentConfig

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


async def test_invalid_config_type() -> None:
    class MyModel(BaseModel, frozen=True):
        pass

    with pytest.raises(TypeError):
        await K8sSandboxEnvironment.sample_init(__file__, MyModel(), {})


def test_can_serialize_and_deserialize_config() -> None:
    original = K8sSandboxEnvironmentConfig(
        chart="my-chart", values=Path("my-values.yaml")
    )

    as_json = original.model_dump()
    recreated = K8sSandboxEnvironmentConfig.model_validate(as_json)

    assert recreated == original
