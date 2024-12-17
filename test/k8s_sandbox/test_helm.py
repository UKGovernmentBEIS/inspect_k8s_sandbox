from pathlib import Path
from unittest.mock import patch

import pytest
from inspect_ai.util import ExecResult

from k8s_sandbox._helm import Release, _run_subprocess


@pytest.fixture
def uninstallable_release() -> Release:
    return Release(__file__, chart_path=Path("/non_existent_chart"))


async def test_helm_install_error(uninstallable_release: Release) -> None:
    with patch("k8s_sandbox._helm._run_subprocess", wraps=_run_subprocess) as spy:
        with pytest.raises(RuntimeError) as excinfo:
            await uninstallable_release.install()

    assert spy.call_count == 1
    assert "not found" in str(excinfo.value)


async def test_helm_resourcequota_retries(uninstallable_release: Release) -> None:
    fail_result = ExecResult(
        False,
        1,
        "",
        "Error: INSTALLATION FAILED: create: failed to create: Operation cannot be "
        'fulfilled on resourcequotas "resource-quota": the object has been '
        "modified; please apply your changes to the latest version and try again\n",
    )

    with patch("k8s_sandbox._helm.INSTALL_RETRY_DELAY_SECONDS", 0):
        with patch(
            "k8s_sandbox._helm._run_subprocess", return_value=fail_result
        ) as mock:
            with pytest.raises(Exception) as excinfo:
                await uninstallable_release.install()

    assert mock.call_count == 3
    assert "resourcequotas" in str(excinfo.value)


@pytest.mark.parametrize("value", ["0", "-1", "abcd"])
async def test_invalid_helm_timeout(
    uninstallable_release: Release, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSPECT_HELM_TIMEOUT", value)

    with pytest.raises(ValueError):
        await uninstallable_release.install()
