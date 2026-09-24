from unittest.mock import patch

import pytest
from inspect_ai._util.error import PrerequisiteError

from k8s_sandbox._prereqs import _parse_version, validate_prereqs


async def test_helm_version_too_low() -> None:
    with patch("k8s_sandbox._prereqs.MINIMUM_HELM_VERSION", "999.0.0"):
        with pytest.raises(PrerequisiteError) as error:
            await validate_prereqs()

        assert error.match("Found version")


async def test_helm_version_satisfactory() -> None:
    await validate_prereqs()


def test_parse_version_strips_trailing_newline() -> None:
    """`helm version --short` output ends in a newline that semver 3.1+ rejects."""
    assert str(_parse_version("v3.21.3+g1ad6e68\n")) == "3.21.3+g1ad6e68"


def test_parse_version_without_trailing_whitespace() -> None:
    assert str(_parse_version("v3.15.3+g3bb50bb")) == "3.15.3+g3bb50bb"
