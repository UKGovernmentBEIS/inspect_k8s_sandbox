from unittest.mock import patch

import pytest
from inspect_ai._util.error import PrerequisiteError
from semver import Version

from k8s_sandbox._prereqs import _parse_version, validate_prereqs


async def test_helm_version_too_low() -> None:
    with patch("k8s_sandbox._prereqs.MINIMUM_HELM_VERSION", "999.0.0"):
        with pytest.raises(PrerequisiteError) as error:
            await validate_prereqs()

        assert error.match("Found version")


async def test_helm_version_satisfactory() -> None:
    await validate_prereqs()


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("v3.15.3+g3bb50bb\n", Version(3, 15, 3, build="g3bb50bb")),
        ("v4.3.0+gbec5b06\n", Version(4, 3, 0, build="gbec5b06")),
        ("4.3.0", Version(4, 3, 0)),
    ],
)
def test_parse_helm_short_version(output: str, expected: Version) -> None:
    assert _parse_version(output) == expected
