import asyncio
import contextlib
import itertools
import json
import logging
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Callable, Iterable
from unittest.mock import MagicMock, patch

import pytest
import yaml
from inspect_ai.util import ExecResult
from pytest import LogCaptureFixture

from k8s_sandbox._helm import (
    _LIST_PODS_READ_TIMEOUT,
    _SERVICE_LABEL,
    DEFAULT_TIMEOUT,
    INSPECT_HELM_LABELS,
    INSPECT_HELM_TIMEOUT,
    INSPECT_HELM_UNINSTALL_TIMEOUT,
    INSPECT_SANDBOX_COREDNS_IMAGE,
    Release,
    StaticValuesSource,
    ValuesSource,
    _get_expected_services,
    _get_helm_major_version,
    _get_timeout,
    _get_uninstall_timeout,
    _get_wait_flag,
    _helm_escape,
    _pod_ready,
    _run_subprocess,
    get_all_release_names,
    uninstall,
    validate_no_null_values,
)
from k8s_sandbox._kubernetes_api import get_default_namespace, k8s_client
from k8s_sandbox._pod.snapshot import PodSnapshot
from k8s_sandbox._sandbox_environment import _key_to_pascal, _metadata_to_extra_values


@pytest.fixture(autouse=True)
def _mock_default_namespace(request: pytest.FixtureRequest) -> object:
    """Patch get_default_namespace for tests that don't need a real cluster.

    Tests marked req_k8s get the real implementation; everything else gets a
    stub so that constructing a Release doesn't require a kubeconfig.
    """
    if "req_k8s" in {m.name for m in request.node.iter_markers()}:
        yield
        return
    with patch("k8s_sandbox._helm.get_default_namespace", return_value="default") as m:
        yield m


def _manifest(*docs: str) -> str:
    return json.dumps({"manifest": "\n---\n".join(docs)})


def _stateful_set(service: str = "default", replicas: int = 1) -> str:
    return (
        f"kind: StatefulSet\nmetadata: {{name: {service}}}\n"
        f"spec:\n  replicas: {replicas}\n  template:\n    metadata:\n"
        f"      labels: {{{_SERVICE_LABEL}: {service}}}\n"
    )


_STATEFUL_SET = _stateful_set()


def _list_item(service: str) -> str:
    return f"{{kind: Pod, metadata: {{labels: {{{_SERVICE_LABEL}: {service}}}}}}}"


_BARE_POD = f"""
kind: Pod
metadata:
  name: p
  labels: {{{_SERVICE_LABEL}: default}}
"""
_TWO_SERVICES = _stateful_set("default") + "---\n" + _stateful_set("victim")


def _helm_installed(mock_run: MagicMock, *docs: str) -> None:
    """Make a mocked `helm install` return a manifest the readiness wait can read."""
    mock_run.return_value = ExecResult(
        True, 0, _manifest(*(docs or (_STATEFUL_SET,))), ""
    )


@pytest.fixture(autouse=True)
def _mock_release_pods(request: pytest.FixtureRequest) -> object:
    """Stop tests which don't need a cluster from polling one for readiness.

    install() waits for the release's pods after the helm subprocess returns, so
    tests which mock _run_subprocess would otherwise reach the Kubernetes API. The
    real wait loop still runs; only the read of the cluster is stubbed.
    """
    if "req_k8s" in {m.name for m in request.node.iter_markers()}:
        yield
        return
    # Stub the cluster read, not _list_release_pods itself: the arguments it builds
    # are what decide whether the poll is bounded and reads the right pods.
    with patch("k8s_sandbox._helm.k8s_client"):
        with patch("k8s_sandbox._helm.list_pods", return_value=[_pod()]) as m:
            yield m


@pytest.fixture
def uninstallable_release() -> Release:
    return Release(
        __file__,
        chart_path=Path("/non_existent_chart"),
        values_source=ValuesSource.none(),
        context_name=None,
    )


@pytest.fixture
def log_err(caplog: LogCaptureFixture) -> LogCaptureFixture:
    # Note: this will prevent lower level messages from being shown in pytest output.
    caplog.set_level(logging.ERROR)
    return caplog


@pytest.mark.req_k8s
async def test_helm_install_error(
    uninstallable_release: Release, log_err: LogCaptureFixture
) -> None:
    with patch("k8s_sandbox._helm._run_subprocess", wraps=_run_subprocess) as spy:
        with pytest.raises(RuntimeError) as excinfo:
            await uninstallable_release.install()

    assert spy.call_count == 1
    assert "not found" in str(excinfo.value)
    assert "not found" in log_err.text


@pytest.mark.req_k8s
async def test_cancelling_install_uninstalls():
    release = Release(__file__, None, ValuesSource.none(), None)
    with patch("k8s_sandbox._helm.uninstall", wraps=uninstall) as spy:
        task = asyncio.create_task(release.install())
        await asyncio.sleep(0.5)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert spy.call_count == 1
    assert release.release_name not in await get_all_release_names(
        get_default_namespace(context_name=None), None
    )


@pytest.mark.req_k8s
async def test_helm_uninstall_does_not_error_for_release_not_found(
    log_err: LogCaptureFixture,
) -> None:
    release = Release(__file__, None, ValuesSource.none(), None)

    # Note: we haven't called install() on release.
    await release.uninstall(quiet=False)

    assert log_err.text == ""


@pytest.mark.req_k8s
async def test_helm_uninstall_errors_for_other_errors(
    log_err: LogCaptureFixture,
) -> None:
    with pytest.raises(RuntimeError) as excinfo:
        await uninstall("my invalid release name!", "fake-namespace", None, quiet=False)

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


@pytest.mark.req_k8s
async def test_helm_install_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "20")
    # A release which can never be scheduled, so this cannot race into success on a
    # cluster which happens to have a warm node.
    values = Path(__file__).parent / "resources" / "unschedulable-values.yaml"
    release = Release(__file__, None, StaticValuesSource(values), None)

    with pytest.raises(RuntimeError) as excinfo:
        await release.install()

    # The timeout names the real cause, not just that it expired.
    assert "Helm release did not become ready within 20s" in str(excinfo.value)
    assert "FailedScheduling" in str(excinfo.value)
    await release.uninstall(quiet=True)


@pytest.mark.parametrize(
    ("value", "expected_create_namespace"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("y", True),
        ("0", False),
        ("false", False),
        ("", False),
        (None, False),
    ],
)
async def test_helm_create_namespace(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected_create_namespace: bool
) -> None:
    if value is None:
        monkeypatch.delenv("INSPECT_HELM_CREATE_NAMESPACE", raising=False)
    else:
        monkeypatch.setenv("INSPECT_HELM_CREATE_NAMESPACE", value)

    release = Release(__file__, None, ValuesSource.none(), None)
    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    mock_run.assert_called_once()
    assert (
        "--create-namespace" in mock_run.call_args[0][1]
    ) == expected_create_namespace


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("foo", "Foo"),
        ("foo bar", "FooBar"),
        ("fooBar", "FooBar"),
        ("fooBarBaz", "FooBarBaz"),
        ("FOO", "Foo"),
        ("test", "Test"),
        ("multi word key", "MultiWordKey"),
        ("camelCaseKey", "CamelCaseKey"),
        ("eval_name", "EvalName"),
        ("eval-name", "EvalName"),
        ("my_camelCase_key", "MyCamelCaseKey"),
    ],
)
def test_key_to_pascal(key: str, expected: str) -> None:
    assert _key_to_pascal(key) == expected


@pytest.mark.parametrize(
    ("metadata", "template_content", "expected"),
    [
        ({}, "", {}),
        (
            {"test": "5"},
            "{{ .Values.sampleMetadataTest }}",
            {"sampleMetadataTest": "5"},
        ),
        (
            {"test name": "abc"},
            "{{ .Values.sampleMetadataTestName }}",
            {"sampleMetadataTestName": "abc"},
        ),
        # camelCase key is converted to PascalCase.
        (
            {"testName": "abc"},
            "{{ .Values.sampleMetadataTestName }}",
            {"sampleMetadataTestName": "abc"},
        ),
        # Metadata key not referenced in templates is excluded.
        (
            {"test": "5"},
            "no references here",
            {},
        ),
        # Only referenced keys are included.
        (
            {"used": "yes", "unused": "no"},
            "{{ .Values.sampleMetadataUsed }}",
            {"sampleMetadataUsed": "yes"},
        ),
        # Underscores are treated as word separators.
        (
            {"eval_name": "foo"},
            "{{ .Values.sampleMetadataEvalName }}",
            {"sampleMetadataEvalName": "foo"},
        ),
        # Hyphens are treated as word separators.
        (
            {"eval-name": "bar"},
            "{{ .Values.sampleMetadataEvalName }}",
            {"sampleMetadataEvalName": "bar"},
        ),
    ],
)
def test_metadata_to_extra_values(
    metadata: dict[str, str],
    template_content: str,
    expected: dict[str, str],
    tmp_path: Path,
) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "test.yaml").write_text(template_content)
    assert _metadata_to_extra_values(metadata, tmp_path, None) == expected


def test_metadata_to_extra_values_checks_values_file(tmp_path: Path) -> None:
    """Metadata referenced in the values file (but not templates) is included."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "test.yaml").write_text("nothing here")
    values_file = tmp_path / "values.yaml"
    values_file.write_text("key: {{ .Values.sampleMetadataFoo }}")
    assert _metadata_to_extra_values({"foo": "bar"}, tmp_path, values_file) == {
        "sampleMetadataFoo": "bar",
    }


def test_metadata_to_extra_values_checks_subcharts(tmp_path: Path) -> None:
    """Metadata referenced in a subchart template is included."""
    subchart_templates = tmp_path / "charts" / "mysubchart" / "templates"
    subchart_templates.mkdir(parents=True)
    (subchart_templates / "deployment.yaml").write_text(
        "{{ .Values.sampleMetadataFoo }}"
    )
    assert _metadata_to_extra_values({"foo": "bar"}, tmp_path, None) == {
        "sampleMetadataFoo": "bar",
    }


async def test_helm_install_extra_values() -> None:
    extra = {"sampleMetadataTestName": "abc", "sampleMetadataTest": "5"}
    release = Release(__file__, None, ValuesSource.none(), None, extra_values=extra)

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][1]
    assert "--set-string=sampleMetadataTestName=abc" in args
    assert "--set-string=sampleMetadataTest=5" in args


async def test_helm_install_no_extra_values() -> None:
    release = Release(__file__, None, ValuesSource.none(), None)

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][1]
    assert not any(arg.startswith("--set-string=sampleMetadata") for arg in args)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("plain", "plain"),
        ("has,comma", "has\\,comma"),
        ("has.dot", "has\\.dot"),
        ("has=equals", "has\\=equals"),
        ("back\\slash", "back\\\\slash"),
        ("a,b.c=d\\e", "a\\,b\\.c\\=d\\\\e"),
    ],
)
def test_helm_escape(value: str, expected: str) -> None:
    assert _helm_escape(value) == expected


async def test_helm_install_extra_values_escaped() -> None:
    extra = {"sampleMetadataKey": "val,with.special=chars"}
    release = Release(__file__, None, ValuesSource.none(), None, extra_values=extra)

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    args = mock_run.call_args[0][1]
    assert "--set-string=sampleMetadataKey=val\\,with\\.special\\=chars" in args


def test_metadata_to_extra_values_skips_invalid_keys(tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "test.yaml").write_text(
        "{{ .Values.sampleMetadataGood }} {{ .Values.sampleMetadataBad.Key }}"
    )
    result = _metadata_to_extra_values(
        {"good": "ok", "bad.key": "nope", "also=bad": "nope"}, tmp_path, None
    )
    assert result == {"sampleMetadataGood": "ok"}


def test_metadata_to_extra_values_skips_clashing_keys(tmp_path: Path) -> None:
    """Later keys that map to the same Helm key as an earlier key are skipped."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "test.yaml").write_text("{{ .Values.sampleMetadataEvalName }}")

    result = _metadata_to_extra_values(
        {"eval_name": "first", "eval-name": "second"}, tmp_path, None
    )

    assert result == {"sampleMetadataEvalName": "first"}


def test_validate_no_null_values_with_valid_data() -> None:
    """Test that validation passes for valid data without null values."""
    valid_data = {
        "services": {"default": {"image": "python:3.12"}},
        "volumes": {"shared": {}},
    }
    # Should not raise
    validate_no_null_values(valid_data, "test-source")


def test_validate_no_null_values_with_top_level_null() -> None:
    """Test that validation catches null values at top level."""
    invalid_data = {"services": {"default": {"image": "python:3.12"}}, "volumes": None}

    with pytest.raises(ValueError) as excinfo:
        validate_no_null_values(invalid_data, "test-source")

    assert "test-source" in str(excinfo.value)
    assert "volumes" in str(excinfo.value)
    assert "null values" in str(excinfo.value)


def test_validate_no_null_values_with_nested_null() -> None:
    """Test that validation catches null values nested in dicts."""
    invalid_data = {
        "services": {"default": {"image": "python:3.12"}},
        "volumes": {"shared": None, "data": {}},
    }

    with pytest.raises(ValueError) as excinfo:
        validate_no_null_values(invalid_data, "test-source")

    assert "volumes.shared" in str(excinfo.value)
    assert "null values" in str(excinfo.value)


def test_validate_no_null_values_with_list_null() -> None:
    """Test that validation catches null values in lists."""
    invalid_data = {
        "services": {"default": {"env": ["VAR1=value1", None, "VAR3=value3"]}}
    }

    with pytest.raises(ValueError) as excinfo:
        validate_no_null_values(invalid_data, "test-source")

    assert "services.default.env[1]" in str(excinfo.value)


def test_validate_no_null_values_with_multiple_nulls() -> None:
    """Test that validation reports all null value paths."""
    invalid_data = {
        "services": {"default": {"image": None}},
        "volumes": {"shared": None, "data": {"nested": None}},
    }

    with pytest.raises(ValueError) as excinfo:
        validate_no_null_values(invalid_data, "test-source")

    error_msg = str(excinfo.value)
    assert "services.default.image" in error_msg
    assert "volumes.shared" in error_msg
    assert "volumes.data.nested" in error_msg


@pytest.mark.parametrize(
    ("env_value", "expected_set_arg"),
    [
        (
            "public.ecr.aws/eks-distro/coredns/coredns:v1.11.4",
            "--set-string=corednsImage=public\\.ecr\\.aws/eks-distro/coredns/coredns:v1\\.11\\.4",
        ),
        (None, None),
    ],
)
async def test_coredns_image_env_var(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    expected_set_arg: str | None,
) -> None:
    if env_value is None:
        monkeypatch.delenv(INSPECT_SANDBOX_COREDNS_IMAGE, raising=False)
    else:
        monkeypatch.setenv(INSPECT_SANDBOX_COREDNS_IMAGE, env_value)

    release = Release(__file__, None, ValuesSource.none(), None)
    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][1]
    if expected_set_arg:
        assert expected_set_arg in args
    else:
        assert not any(arg.startswith("--set-string=corednsImage=") for arg in args)


def test_static_values_source_with_valid_file() -> None:
    """Test that StaticValuesSource accepts valid values file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        data = {
            "services": {"default": {"image": "python:3.12"}},
            "volumes": {"shared": {}},
        }
        yaml.dump(data, f)
        temp_path = Path(f.name)

    try:
        # Should not raise
        source = StaticValuesSource(temp_path)
        with source.values_file() as values_file:
            assert values_file == temp_path
    finally:
        temp_path.unlink()


def test_static_values_source_with_empty_file() -> None:
    """Test that StaticValuesSource handles empty YAML files."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("")  # Empty file
        temp_path = Path(f.name)

    try:
        # Should not raise for empty file
        source = StaticValuesSource(temp_path)
        with source.values_file() as values_file:
            assert values_file == temp_path
    finally:
        temp_path.unlink()


@pytest.fixture(autouse=False)
def _clear_wait_flag_cache() -> None:
    """Clear the lru_cache on _get_wait_flag before each test that uses it."""
    _get_wait_flag.cache_clear()


@pytest.mark.parametrize(
    ("version_output", "expected_major"),
    [
        ("v3.16.1+gad4f7f0", 3),
        ("v4.0.0", 4),
        ("v4.1.2+gabcdef0", 4),
    ],
)
def test_get_helm_major_version(version_output: str, expected_major: int) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = version_output
        assert _get_helm_major_version() == expected_major


def test_get_helm_major_version_returns_none_on_error() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _get_helm_major_version() is None


@pytest.mark.usefixtures("_clear_wait_flag_cache")
def test_get_wait_flag_helm3() -> None:
    with patch("k8s_sandbox._helm._get_helm_major_version", return_value=3):
        assert _get_wait_flag() == "--wait"


@pytest.mark.usefixtures("_clear_wait_flag_cache")
def test_get_wait_flag_helm4() -> None:
    with patch("k8s_sandbox._helm._get_helm_major_version", return_value=4):
        assert _get_wait_flag() == "--wait=legacy"


@pytest.mark.usefixtures("_clear_wait_flag_cache")
def test_get_wait_flag_helm5() -> None:
    with patch("k8s_sandbox._helm._get_helm_major_version", return_value=5):
        assert _get_wait_flag() == "--wait=legacy"


@pytest.mark.usefixtures("_clear_wait_flag_cache")
def test_get_wait_flag_returns_wait_on_failure() -> None:
    with patch("k8s_sandbox._helm._get_helm_major_version", return_value=None):
        assert _get_wait_flag() == "--wait"


@pytest.mark.usefixtures("_clear_wait_flag_cache")
def test_get_wait_flag_is_cached() -> None:
    with patch("k8s_sandbox._helm._get_helm_major_version", return_value=3) as mock:
        _get_wait_flag()
        _get_wait_flag()
        mock.assert_called_once()


@pytest.mark.parametrize(
    ("env_value", "expected_labels_arg"),
    [
        ("ci-branch=my-feature", "--labels=ci-branch=my-feature,inspectSandbox=true"),
        (
            "ci-branch=my-feature,run-id=42",
            "--labels=ci-branch=my-feature,run-id=42,inspectSandbox=true",
        ),
        (None, "--labels=inspectSandbox=true"),
        ("", "--labels=inspectSandbox=true"),
    ],
)
async def test_helm_labels_env_var(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    expected_labels_arg: str,
) -> None:
    if env_value is None:
        monkeypatch.delenv(INSPECT_HELM_LABELS, raising=False)
    else:
        monkeypatch.setenv(INSPECT_HELM_LABELS, env_value)

    release = Release(__file__, None, ValuesSource.none(), None)
    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][1]
    assert expected_labels_arg in args


@pytest.mark.req_k8s
@pytest.mark.parametrize(
    "env_value",
    [
        "no-equals-sign",
        "x=y, a=b",
    ],
)
async def test_helm_labels_misformatted(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
) -> None:
    """Misformatted INSPECT_HELM_LABELS are passed through to Helm.

    Helm fails with a Kubernetes label validation error.
    """
    monkeypatch.setenv(INSPECT_HELM_LABELS, env_value)
    release = Release(__file__, None, ValuesSource.none(), None)

    with pytest.raises(RuntimeError, match="Invalid value"):
        await release.install()


async def test_helm_labels_cannot_override_inspect_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-specified labels must not set the inspectSandbox label."""
    monkeypatch.setenv(INSPECT_HELM_LABELS, "inspectSandbox=false")
    release = Release(__file__, None, ValuesSource.none(), None)

    with pytest.raises(ValueError, match="inspectSandbox"):
        await release.install()


@pytest.mark.req_k8s
@pytest.mark.parametrize(
    ("env_value", "expected_labels"),
    [
        ("ci-branch=test-label", {"ci-branch": "test-label"}),
        (
            "ci-branch=test-label,run-id=42",
            {"ci-branch": "test-label", "run-id": "42"},
        ),
    ],
)
async def test_helm_labels_appear_on_release_secret(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_labels: dict[str, str],
) -> None:
    """Verify that INSPECT_HELM_LABELS labels are stored on the Helm release secret."""
    monkeypatch.setenv(INSPECT_HELM_LABELS, env_value)
    namespace = get_default_namespace(context_name=None)
    release = Release(__file__, None, ValuesSource.none(), None)
    try:
        await release.install()
        secrets = k8s_client(None).list_namespaced_secret(
            namespace,
            label_selector=f"owner=helm,name={release.release_name}",
        )
        assert len(secrets.items) == 1
        secret_labels = secrets.items[0].metadata
        assert secret_labels is not None
        assert secret_labels.labels is not None
        assert secret_labels.labels.get("inspectSandbox") == "true"
        for key, value in expected_labels.items():
            assert secret_labels.labels.get(key) == value
    finally:
        await release.uninstall(quiet=True)


async def test_install_error_includes_pod_diagnostics() -> None:
    release = Release(
        __file__,
        chart_path=Path("/non_existent_chart"),
        values_source=ValuesSource.none(),
        context_name=None,
    )
    result = ExecResult(
        success=False,
        returncode=1,
        stdout="",
        stderr="Error: INSTALLATION FAILED: resource Pod/default/x not ready.\n",
    )
    diagnostics = "container 'default': last terminated OOMKilled (exit code 137)"

    with patch("k8s_sandbox._helm.describe_release_pods", return_value=diagnostics):
        with pytest.raises(RuntimeError) as excinfo:
            await release._raise_install_error(result)

    assert "OOMKilled" in str(excinfo.value)
    assert "137" in str(excinfo.value)


async def test_install_error_omits_diagnostics_when_unavailable() -> None:
    release = Release(
        __file__,
        chart_path=Path("/non_existent_chart"),
        values_source=ValuesSource.none(),
        context_name=None,
    )
    result = ExecResult(
        success=False,
        returncode=1,
        stdout="",
        stderr="Error: INSTALLATION FAILED: resource Pod/default/x not ready.\n",
    )

    with patch("k8s_sandbox._helm.describe_release_pods", return_value=None):
        with pytest.raises(RuntimeError) as excinfo:
            await release._raise_install_error(result)

    assert "Helm install failed." in str(excinfo.value)


def _pod(
    *,
    ready: bool = True,
    phase: str = "Running",
    name: str = "p",
    service: str | None = "default",
) -> PodSnapshot:
    return PodSnapshot(
        name=name,
        uid=name,
        labels={_SERVICE_LABEL: service} if service is not None else {},
        container_names=("default",),
        container_statuses=(),
        phase=phase,
        ready=ready,
    )


_EVICTED = _pod(name="evicted", ready=False, phase="Failed")


@pytest.mark.parametrize(
    ("pod", "expected"),
    [
        pytest.param(_pod(ready=True), True, id="ready"),
        pytest.param(_pod(ready=False, phase="Pending"), False, id="pending"),
        pytest.param(_pod(ready=False, phase="Failed"), False, id="failed"),
        # Reports Ready=False with reason PodCompleted, which Helm blocks on.
        pytest.param(_pod(ready=False, phase="Succeeded"), True, id="completed"),
    ],
)
def test_pod_ready(pod: PodSnapshot, expected: bool) -> None:
    assert _pod_ready(pod) is expected


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        pytest.param(_manifest(_STATEFUL_SET), {"default"}, id="one-service"),
        pytest.param(
            _manifest(_TWO_SERVICES), {"default", "victim"}, id="two-services"
        ),
        # The documented custom-chart shape.
        pytest.param(_manifest(_BARE_POD), {"default"}, id="bare-pod"),
        # Replicas do not add sandboxes: get_sandbox_pods() keys one entry per label.
        pytest.param(
            _manifest(_stateful_set("default", replicas=3)), {"default"}, id="replicas"
        ),
        # A pod the chart does not declare to be a sandbox cannot serve as one.
        pytest.param(
            _manifest(_STATEFUL_SET, "kind: DaemonSet\nspec:\n  template:\n    {}\n"),
            {"default"},
            id="daemonset-declares-nothing",
        ),
        # A controller's own labels are not its pod template's: reading them invents
        # a sandbox no pod will ever serve.
        pytest.param(
            _manifest(
                "kind: StatefulSet\n"
                f"metadata:\n  labels: {{{_SERVICE_LABEL}: phantom}}\n"
                f"spec:\n  template:\n    metadata:\n"
                f"      labels: {{{_SERVICE_LABEL}: default}}\n"
            ),
            {"default"},
            id="controller-metadata-is-not-a-pod-template",
        ),
        # The same label appears on objects which select pods rather than make them.
        pytest.param(
            _manifest(
                _STATEFUL_SET,
                f"kind: Service\nspec:\n  selector: {{{_SERVICE_LABEL}: phantom}}\n",
                "kind: CiliumNetworkPolicy\nspec:\n  endpointSelector:\n"
                f"    matchLabels: {{{_SERVICE_LABEL}: phantom}}\n",
            ),
            {"default"},
            id="selectors-are-not-sandboxes",
        ),
        pytest.param(
            _manifest(
                "kind: List\nitems: [" + _list_item("a") + ", " + _list_item("b") + "]"
            ),
            {"a", "b"},
            id="list-wrapping-pods",
        ),
    ],
)
def test_expected_services(stdout: str, expected: set[str]) -> None:
    assert _get_expected_services(stdout, "abcdefgh") == expected


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("not json", id="unparseable"),
        pytest.param("{}", id="no-manifest-key"),
        pytest.param('{"manifest": null}', id="null-manifest"),
        # Treating "no sandbox declared" as "nothing to wait for" is how a release
        # gets handed over before its pods exist.
        pytest.param(
            _manifest("kind: ConfigMap\nmetadata: {name: c}\n"),
            id="declares-no-sandbox",
        ),
    ],
)
def test_expected_services_fails_closed(stdout: str) -> None:
    with pytest.raises(RuntimeError, match="manifest|declares no Pod"):
        _get_expected_services(stdout, "abcdefgh")


async def _wait(
    release: Release, polls: Iterable[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the readiness wait against a scripted sequence of poll results.

    Asserts the script is consumed exactly, so that a test which expects the wait to
    keep polling fails if it returns early instead of passing for the wrong reason.
    """
    mock = MagicMock(side_effect=polls)
    monkeypatch.setattr(Release, "_list_release_pods", mock, raising=True)
    monkeypatch.setattr("k8s_sandbox._helm._READINESS_POLL_INTERVAL", 0)
    # A short deadline of its own: the wait treats an exhausted script as a failed
    # poll and would otherwise spin for the whole INSPECT_HELM_TIMEOUT.
    await release._wait_until_ready(time.monotonic() + 2)
    if isinstance(polls, list):
        assert mock.call_count == len(polls), (
            f"expected the wait to read the pods {len(polls)} times, not "
            f"{mock.call_count}"
        )


async def test_wait_until_ready_returns_once_every_pod_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = Release(__file__, None, ValuesSource.none(), None)
    release._expected_services = frozenset({"default"})

    await _wait(
        release,
        [
            # The evicted pod is terminal and never becomes ready; a controller
            # replaces it, so it must not hold the release back.
            [_pod(name="a"), _pod(name="b", ready=False), _EVICTED],
            [_pod(name="a"), _pod(name="b"), _EVICTED],  # the cached poll says ready
            [_pod(name="a"), _pod(name="b"), _EVICTED],  # and a consistent read agrees
        ],
        monkeypatch,
    )


async def test_wait_until_ready_keeps_waiting_for_a_pod_that_does_not_exist_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The point of naming the sandboxes: one of two being Ready is not ready, even
    # though every pod currently visible is.
    release = Release(__file__, None, ValuesSource.none(), None)
    release._expected_services = frozenset({"default", "victim"})
    both = [_pod(name="a", service="default"), _pod(name="b", service="victim")]
    polls = [
        [_pod(name="a", service="default")],
        [_pod(name="a", service="default")],
        both,
        both,  # the confirming consistent read
    ]

    await _wait(release, polls, monkeypatch)


async def test_wait_until_ready_keeps_waiting_while_no_pods_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = Release(__file__, None, ValuesSource.none(), None)
    release._expected_services = frozenset({"default"})

    await _wait(release, [[], [], [_pod()], [_pod()]], monkeypatch)


async def test_wait_until_ready_ignores_pods_which_are_not_sandboxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A DaemonSet's pods and a completed hook pod carry the release label but declare
    # no sandbox, so they cannot stand in for one.
    release = Release(__file__, None, ValuesSource.none(), None)
    release._expected_services = frozenset({"default"})
    strangers = [
        _pod(name="ds-1", service=None),
        _pod(name="ds-2", service=None),
        # A completed hook pod, even one claiming the sandbox's name: nothing can be
        # exec'd into it, so it must not stand in for the sandbox.
        _pod(name="hook", service="default", ready=False, phase="Succeeded"),
    ]
    sandbox = _pod(name="default-0", service="default")

    await _wait(
        release, [strangers, [*strangers, sandbox], [*strangers, sandbox]], monkeypatch
    )


async def test_wait_until_ready_retries_a_transient_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = Release(__file__, None, ValuesSource.none(), None)
    release._expected_services = frozenset({"default"})

    await _wait(release, [ConnectionError("boom"), [_pod()], [_pod()]], monkeypatch)


async def test_wait_until_ready_times_out_with_diagnostics(
    monkeypatch: pytest.MonkeyPatch, log_err: LogCaptureFixture
) -> None:
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "1")
    monkeypatch.setattr(
        "k8s_sandbox._helm.describe_release_pods", lambda *_: "ImagePullBackOff"
    )
    release = Release(__file__, None, ValuesSource.none(), None)

    with pytest.raises(RuntimeError, match="did not become ready within 1s"):
        await _wait(release, itertools.repeat([_pod(ready=False)]), monkeypatch)

    assert "ImagePullBackOff" in log_err.text


async def test_wait_until_ready_says_so_when_the_chart_made_no_pods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "1")
    monkeypatch.setattr("k8s_sandbox._helm.describe_release_pods", lambda *_: None)
    release = Release(__file__, None, ValuesSource.none(), None)
    release._expected_services = frozenset({"default"})

    with pytest.raises(RuntimeError, match="created no pods labelled"):
        await _wait(release, itertools.repeat([]), monkeypatch)


@pytest.mark.parametrize(
    ("allow_stale", "resource_version"),
    [pytest.param(True, "0", id="polling"), pytest.param(False, None, id="confirming")],
)
def test_list_release_pods_reads_the_cache_only_when_polling(
    allow_stale: bool, resource_version: str | None, _mock_release_pods: MagicMock
) -> None:
    # A repeated poll may read a stale answer; the read confirming a ready verdict
    # may not. Unbounded, neither leaves the deadline enforceable.
    release = Release(__file__, None, ValuesSource.none(), None)

    release._list_release_pods(allow_stale)

    kwargs = _mock_release_pods.call_args.kwargs
    assert kwargs["resource_version"] == resource_version
    assert kwargs["request_timeout"] == _LIST_PODS_READ_TIMEOUT
    assert kwargs["label_selector"] == (
        f"app.kubernetes.io/instance={release.release_name}"
    )


async def test_the_timeout_covers_submission_as_well_as_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # INSPECT_HELM_TIMEOUT is one budget: a slow submission eats into the readiness
    # wait rather than being free.
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "100")
    # Submission takes 30 of the 100 seconds. Replace the module's `time` binding, not
    # time.monotonic itself, which is shared with the whole process.
    clock = itertools.chain([1_000.0, 1_030.0], itertools.repeat(1_030.0))
    monkeypatch.setattr(
        "k8s_sandbox._helm.time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    deadlines: list[float] = []

    async def capture(self: Release, deadline: float) -> list[PodSnapshot]:
        deadlines.append(deadline)
        return [_pod()]

    monkeypatch.setattr(Release, "_wait_until_ready", capture)
    release = Release(__file__, None, ValuesSource.none(), None)

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await release.install()

    assert deadlines == [1_100.0]
    # Helm gets what is left of the budget, not a fresh copy of it.
    timeouts = [a for a in mock_run.call_args[0][1] if a.startswith("--timeout=")]
    assert timeouts == ["--timeout=70s"]


async def test_install_takes_the_pod_count_from_helm_and_waits_off_the_subprocess(
    _mock_release_pods: MagicMock,
) -> None:
    release = Release(__file__, None, ValuesSource.none(), None)
    _mock_release_pods.return_value = [
        _pod(name="a", service="default"),
        _pod(name="b", service="victim"),
    ]

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run, _TWO_SERVICES)
        await release.install()

    args = mock_run.call_args[0][1]
    assert "--output=json" in args
    # The readiness wait happens off the install permit, not inside the subprocess.
    assert not [a for a in args if a.startswith("--wait")]
    # Both services, so this cannot pass on Release's empty default.
    assert release._expected_services == {"default", "victim"}
    # Handover uses the pods readiness confirmed, so nothing can change in between.
    _mock_release_pods.return_value = []
    assert set(await release.get_sandbox_pods()) == {"default", "victim"}
    # Guards against the wait being dropped: deleting the call from install() would
    # otherwise only make other tests hang rather than fail.
    _mock_release_pods.assert_called()


async def test_install_permit_is_released_before_the_readiness_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A release waiting for capacity must not stop another release, whose capacity
    # is available, from being submitted at all.
    # Patch the semaphore rather than INSPECT_MAX_HELM_INSTALL: Inspect's concurrency
    # registry caches by name, so the env var is a no-op once it has been created.
    semaphore = asyncio.Semaphore(1)

    @contextlib.asynccontextmanager
    async def one_permit() -> AsyncGenerator[None, None]:
        async with semaphore:
            yield

    waiting = asyncio.Event()
    submitted: list[str] = []

    async def block_forever(self: Release, deadline: float) -> None:
        waiting.set()
        await asyncio.sleep(3600)

    async def record(cmd: str, args: list[str], capture_output: bool) -> ExecResult:
        submitted.append(args[0])
        return ExecResult(True, 0, _manifest(_STATEFUL_SET), "")

    monkeypatch.setattr("k8s_sandbox._helm._install_semaphore", one_permit)
    monkeypatch.setattr("k8s_sandbox._helm._run_subprocess", record)
    monkeypatch.setattr(Release, "_wait_until_ready", block_forever)

    blocked = asyncio.create_task(
        Release(__file__, None, ValuesSource.none(), None).install()
    )
    await asyncio.wait_for(waiting.wait(), timeout=5)

    # The first release is mid-wait; the second must still be able to submit.
    monkeypatch.setattr(
        Release, "_wait_until_ready", lambda self, deadline: asyncio.sleep(0)
    )
    await asyncio.wait_for(
        Release(__file__, None, ValuesSource.none(), None).install(), timeout=5
    )

    assert submitted == ["install", "install"]
    blocked.cancel()
    await asyncio.gather(blocked, return_exceptions=True)


@pytest.mark.parametrize(
    ("env", "expected"),
    [(None, f"{DEFAULT_TIMEOUT}s"), ("30", "30s")],
)
async def test_uninstall_timeout_is_independent_of_the_install_timeout(
    env: str | None, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # INSPECT_HELM_TIMEOUT is legitimately set to hours; an uninstall holds an
    # uninstall permit and, on the error path, the sample's sandbox slot.
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "86400")
    if env is not None:
        monkeypatch.setenv(INSPECT_HELM_UNINSTALL_TIMEOUT, env)

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
        _helm_installed(mock_run)
        await uninstall("abcdefgh", "default", None, quiet=True)

    args = mock_run.call_args[0][1]
    assert args[args.index("--timeout") + 1] == expected


@pytest.mark.parametrize("value", ["0", "-1", "abcd"])
@pytest.mark.parametrize(
    ("get_timeout", "env_var"),
    [
        (_get_timeout, INSPECT_HELM_TIMEOUT),
        (_get_uninstall_timeout, INSPECT_HELM_UNINSTALL_TIMEOUT),
    ],
)
def test_invalid_timeout_env_var(
    get_timeout: Callable[[], int],
    env_var: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env_var, value)

    with pytest.raises(ValueError, match=env_var):
        get_timeout()
