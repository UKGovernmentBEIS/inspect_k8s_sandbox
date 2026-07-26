import logging
from typing import cast

from pytest import LogCaptureFixture

from k8s_sandbox._helm import Release
from k8s_sandbox._manager import HelmReleaseManager


class _FakeRelease:
    """Stands in for a Release so uninstall_all can be driven without a cluster."""

    def __init__(self, release_name: str, error: Exception | None = None) -> None:
        self.release_name = release_name
        self._error = error
        self.uninstall_attempted = False

    async def install(self) -> None:
        return None

    async def uninstall(self, quiet: bool) -> None:
        self.uninstall_attempted = True
        if self._error is not None:
            raise self._error


async def _install(manager: HelmReleaseManager, release: _FakeRelease) -> None:
    await manager.install(cast(Release, release))


async def test_uninstall_all_reports_a_failed_uninstall(
    caplog: LogCaptureFixture,
) -> None:
    manager = HelmReleaseManager()
    healthy = _FakeRelease("aaaaaaaa")
    failing = _FakeRelease("bbbbbbbb", RuntimeError("Helm uninstall failed."))
    await _install(manager, healthy)
    await _install(manager, failing)

    with caplog.at_level(logging.ERROR):
        await manager.uninstall_all(print_only=False)

    # Both were attempted: one failure must not prevent the others being uninstalled.
    assert healthy.uninstall_attempted
    assert failing.uninstall_attempted
    # The failure is named, along with how to remove the release that is still there.
    assert "bbbbbbbb" in caplog.text
    assert "inspect sandbox cleanup k8s bbbbbbbb" in caplog.text
    # The release which uninstalled cleanly is not reported as a failure.
    assert "aaaaaaaa" not in caplog.text


async def test_uninstall_all_silent_when_every_uninstall_succeeds(
    caplog: LogCaptureFixture,
) -> None:
    manager = HelmReleaseManager()
    await _install(manager, _FakeRelease("aaaaaaaa"))
    await _install(manager, _FakeRelease("bbbbbbbb"))

    with caplog.at_level(logging.ERROR):
        await manager.uninstall_all(print_only=False)

    assert caplog.text == ""
