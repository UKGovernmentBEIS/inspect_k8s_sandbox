import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from k8s_sandbox import K8sSandboxEnvironment, K8sSandboxEnvironmentConfig
from k8s_sandbox._sandbox_environment import validate_k8s_name, validate_service_names

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


def test_valid_k8s_name() -> None:
    valid_names = [
        "myservice",
        "my-service",
        "my.service",
        "myservice123",
        "a" * 63,  # Max length
        "a",  # Min length
    ]

    for name in valid_names:
        is_valid, _ = validate_k8s_name(name)
        assert is_valid, f"Expected '{name}' to be valid"


def test_invalid_k8s_name() -> None:
    """Test that invalid Kubernetes service names are rejected."""
    invalid_names = [
        "",  # Empty
        "a" * 64,  # Too long
        "-myservice",  # Starts with hyphen
        "myservice-",  # Ends with hyphen
        "my_service",  # Contains underscore
        "MyService",  # Contains uppercase
        "my service",  # Contains space
    ]

    for name in invalid_names:
        is_valid, error = validate_k8s_name(name)
        assert not is_valid, f"Expected '{name}' to be invalid"
        assert error, "Error message should not be empty"


async def test_invalid_service_names() -> None:
    invalid_service_names = {
        "Invalid_Service": "must consist only of lowercase alphanumeric characters",
        "-invalid-start": "must start with an alphanumeric character",
        "invalid-end-": "must end with an alphanumeric character",
        "too-long" + "x" * 60: "is too long (max 63 characters)",
    }

    # Create a services dict for the values file
    invalid_services_config = {
        "services": {name: {"image": "nginx"} for name in invalid_service_names}
    }

    with pytest.raises(ValueError) as excinfo:
        validate_service_names(invalid_services_config)

    error_lines = str(excinfo.value).splitlines()

    assert "Invalid Kubernetes service name(s) in values file:" in error_lines[0]
    assert any("Service names must:" in line for line in error_lines)

    for service_name, expected_error in invalid_service_names.items():
        # Find the line containing the given service name
        service_error_line = next(
            (line for line in error_lines if service_name in line), None
        )
        assert service_error_line is not None, f"No error line found for {service_name}"

        # Check that the line contains the expected error message
        assert expected_error in service_error_line, (
            f"Error for {service_name} doesn't contain '{expected_error}'. "
            f"Actual: {service_error_line}"
        )


async def test_invalid_yaml_syntax() -> None:
    """Test that values file with invalid YAML syntax raises a YAML error."""
    invalid_yaml = """
    services:
      valid-service:
        image: nginx
      # Invalid YAML - missing colon
      another-service
        image: ubuntu
    """

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as temp_file:
        temp_file.write(invalid_yaml)
        temp_file.flush()

        with pytest.raises(yaml.YAMLError):
            await K8sSandboxEnvironment.sample_init(__file__, temp_file.name, {})
