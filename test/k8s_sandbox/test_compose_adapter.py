from pathlib import Path

import pytest
import yaml

from k8s_sandbox._compose_adapter import convert


@pytest.fixture
def resources() -> Path:
    return Path(__file__).parent / "resources" / "compose" / "basic"


def test_converter(resources) -> None:
    expected = (resources / "helm-values.yaml").read_text()

    result = convert(resources / "compose.yaml")
    actual = yaml.dump(result, sort_keys=False)

    assert actual == expected
