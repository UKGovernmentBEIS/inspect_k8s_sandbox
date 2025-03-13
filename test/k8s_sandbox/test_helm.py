import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from inspect_ai.util import ExecResult
from pytest import LogCaptureFixture

from k8s_sandbox._helm import (
    INSPECT_HELM_TIMEOUT,
    Release,
    _run_subprocess,
    uninstall,
)


@pytest.fixture
def uninstallable_release() -> Release:
    return Release(__file__, chart_path=Path("/non_existent_chart"))


@pytest.fixture
def log_err(caplog: LogCaptureFixture) -> LogCaptureFixture:
    # Note: this will prevent lower level messages from being shown in pytest output.
    caplog.set_level(logging.ERROR)
    return caplog


async def test_helm_install_error(
    uninstallable_release: Release, log_err: LogCaptureFixture
) -> None:
    with patch("k8s_sandbox._helm._run_subprocess", wraps=_run_subprocess) as spy:
        with pytest.raises(RuntimeError) as excinfo:
            await uninstallable_release.install()

    assert spy.call_count == 1
    assert "not found" in str(excinfo.value)
    assert "not found" in log_err.text


async def test_helm_uninstall_does_not_error_for_release_not_found(
    log_err: LogCaptureFixture,
) -> None:
    release = Release(__file__)

    # Note: we haven't called install() on release.
    await release.uninstall(quiet=False)

    assert log_err.text == ""


async def test_helm_uninstall_errors_for_other_errors(
    log_err: LogCaptureFixture,
) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        await uninstall("my invalid release name!", "fake-namespace", quiet=False)

    assert "Release name is invalid" in log_err.text
    assert "Release name is invalid" in str(excinfo.value)


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
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, value)

    with pytest.raises(ValueError):
        await uninstallable_release.install()


async def test_helm_install_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "1")

    with pytest.raises(RuntimeError) as excinfo:
        await Release(__file__).install()

    # Verify that we detect the install timeout and add our own message.
    assert "The configured timeout value was 1s. Please see the docs" in str(
        excinfo.value
    )
