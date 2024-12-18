from unittest.mock import patch

import pytest
from inspect_ai._util.error import PrerequisiteError

from k8s_sandbox._prereqs import validate_prereqs


async def test_helm_version_too_low() -> None:
    with patch("k8s_sandbox._prereqs.MINIMUM_HELM_VERSION", "999.0.0"):
        with pytest.raises(PrerequisiteError) as error:
            await validate_prereqs()

        assert error.match("Found version")


async def test_helm_version_satisfactory() -> None:
    await validate_prereqs()
