import logging
from typing import cast

import pytest
from inspect_ai._util.error import PrerequisiteError
from pytest import CaptureFixture, LogCaptureFixture

import k8s_sandbox._manager as manager_module
from k8s_sandbox._helm import Release
from k8s_sandbox._manager import (
    HelmReleaseManager,
    uninstall_all_unmanaged_releases,
)


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


def _stub_unmanaged_releases(
    monkeypatch: pytest.MonkeyPatch,
    releases: list[str],
    failing: set[str],
    confirm: bool = True,
) -> list[str]:
    """Stubs out the cluster, returning the list which records uninstall attempts."""
    attempted: list[str] = []

    async def fake_get_all_release_names(namespace: str, context_name: str | None):
        return releases

    async def fake_uninstall(
        release_name: str, namespace: str, context_name: str | None, quiet: bool
    ) -> None:
        attempted.append(release_name)
        if release_name in failing:
            raise RuntimeError(f"Helm uninstall failed. {release_name}")

    monkeypatch.setattr(
        manager_module, "get_default_namespace", lambda context_name: "default"
    )
    monkeypatch.setattr(
        manager_module, "get_all_release_names", fake_get_all_release_names
    )
    monkeypatch.setattr(manager_module, "helm_uninstall", fake_uninstall)
    monkeypatch.setattr(manager_module.Confirm, "ask", lambda *args, **kwargs: confirm)
    return attempted


async def test_cleanup_all_reports_failures_and_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    attempted = _stub_unmanaged_releases(
        monkeypatch, ["aaaaaaaa", "bbbbbbbb"], failing={"bbbbbbbb"}
    )

    with pytest.raises(PrerequisiteError) as exc_info:
        await uninstall_all_unmanaged_releases()

    assert attempted == ["aaaaaaaa", "bbbbbbbb"]
    assert "Failed to uninstall 1 of 2" in str(exc_info.value.message)
    output = capsys.readouterr().out
    assert "inspect sandbox cleanup k8s bbbbbbbb" in output
    assert "inspect sandbox cleanup k8s aaaaaaaa" not in output
    assert "Complete." not in output


async def test_cleanup_all_completes_when_every_uninstall_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    attempted = _stub_unmanaged_releases(
        monkeypatch, ["aaaaaaaa", "bbbbbbbb"], failing=set()
    )

    await uninstall_all_unmanaged_releases()

    assert attempted == ["aaaaaaaa", "bbbbbbbb"]
    output = capsys.readouterr().out
    assert "Complete." in output
    assert "failed to uninstall" not in output.casefold()


async def test_cleanup_all_uninstalls_nothing_when_not_confirmed(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    attempted = _stub_unmanaged_releases(
        monkeypatch, ["aaaaaaaa"], failing={"aaaaaaaa"}, confirm=False
    )

    await uninstall_all_unmanaged_releases()

    assert attempted == []
    assert "Cancelled." in capsys.readouterr().out
