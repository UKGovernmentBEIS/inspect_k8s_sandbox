import logging
from typing import cast
from unittest.mock import patch

import pytest
from inspect_ai.util import SandboxEnvironment
from pytest import CaptureFixture, LogCaptureFixture

import k8s_sandbox._manager as manager_module
from k8s_sandbox._helm import Release
from k8s_sandbox._manager import (
    HelmReleaseManager,
    uninstall_all_unmanaged_releases,
)
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment


class _FakeRelease:
    """Stands in for a Release so uninstall_all can be driven without a cluster."""

    def __init__(
        self,
        release_name: str,
        error: Exception | None = None,
        context_name: str | None = None,
    ) -> None:
        self.release_name = release_name
        self.namespace = "my-namespace"
        self.context_name = context_name
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
    assert "my-namespace" in caplog.text
    # The release which uninstalled cleanly is not reported as a failure.
    assert "aaaaaaaa" not in caplog.text


async def test_uninstall_all_names_the_context_a_release_was_installed_with(
    caplog: LogCaptureFixture,
) -> None:
    manager = HelmReleaseManager()
    failing = _FakeRelease(
        "bbbbbbbb", RuntimeError("Helm uninstall failed."), context_name="other-cluster"
    )
    await _install(manager, failing)

    with caplog.at_level(logging.ERROR):
        await manager.uninstall_all(print_only=False)

    assert "other-cluster" in caplog.text


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


async def test_cleanup_all_reports_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    attempted = _stub_unmanaged_releases(
        monkeypatch, ["aaaaaaaa", "bbbbbbbb"], failing={"bbbbbbbb"}
    )

    failed = await uninstall_all_unmanaged_releases()

    assert attempted == ["aaaaaaaa", "bbbbbbbb"]
    assert failed == ["bbbbbbbb"]
    output = capsys.readouterr().out
    assert "Failed to uninstall 1 of 2" in output
    assert "inspect sandbox cleanup k8s bbbbbbbb" in output
    assert "inspect sandbox cleanup k8s aaaaaaaa" not in output
    assert "Complete." not in output


async def test_cli_cleanup_all_exits_non_zero_when_a_release_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    _stub_unmanaged_releases(monkeypatch, ["aaaaaaaa"], failing={"aaaaaaaa"})

    with pytest.raises(SystemExit) as exc_info:
        await K8sSandboxEnvironment.cli_cleanup(None)

    assert exc_info.value.code == 1


async def test_cli_cleanup_all_exits_zero_when_every_uninstall_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    _stub_unmanaged_releases(monkeypatch, ["aaaaaaaa"], failing=set())

    await K8sSandboxEnvironment.cli_cleanup(None)

    assert "Complete." in capsys.readouterr().out


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


class _FakeSandbox:
    """Stands in for a K8sSandboxEnvironment; sample_cleanup only reads .release."""

    def __init__(self, release: _FakeRelease) -> None:
        self.release = release


async def _sample_cleanup(manager: HelmReleaseManager, release: _FakeRelease) -> None:
    environments = {"default": cast(SandboxEnvironment, _FakeSandbox(release))}
    with patch.object(HelmReleaseManager, "get_instance", return_value=manager):
        await K8sSandboxEnvironment.sample_cleanup(
            "my-task", None, environments, interrupted=False
        )


async def test_sample_cleanup_uninstalls_and_untracks_the_release() -> None:
    manager = HelmReleaseManager()
    release = _FakeRelease("aaaaaaaa")
    await _install(manager, release)

    await _sample_cleanup(manager, release)

    assert release.uninstall_attempted
    assert manager._installed_releases == []


async def test_sample_cleanup_does_not_fail_the_sample_when_uninstall_fails(
    caplog: LogCaptureFixture,
) -> None:
    # Inspect turns an exception from sample_cleanup() into a sample error, which
    # would discard a sample that had otherwise succeeded.
    manager = HelmReleaseManager()
    failing = _FakeRelease("bbbbbbbb", RuntimeError("Helm uninstall failed."))
    await _install(manager, failing)

    with caplog.at_level(logging.WARNING):
        await _sample_cleanup(manager, failing)

    assert "bbbbbbbb" in caplog.text
    # Still tracked, so that uninstall_all() retries it at the end of the eval.
    assert manager._installed_releases == [failing]
