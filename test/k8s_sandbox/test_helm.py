import asyncio
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from inspect_ai.util import ExecResult
from pytest import LogCaptureFixture

from k8s_sandbox._helm import (
    INSPECT_HELM_TIMEOUT,
    Release,
    StaticValuesSource,
    ValuesSource,
    _helm_escape,
    _run_subprocess,
    get_all_release_names,
    uninstall,
    validate_no_null_values,
)
from k8s_sandbox._kubernetes_api import get_default_namespace
from k8s_sandbox._sandbox_environment import _metadata_to_extra_values


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


async def test_helm_install_error(
    uninstallable_release: Release, log_err: LogCaptureFixture
) -> None:
    with patch("k8s_sandbox._helm._run_subprocess", wraps=_run_subprocess) as spy:
        with pytest.raises(RuntimeError) as excinfo:
            await uninstallable_release.install()

    assert spy.call_count == 1
    assert "not found" in str(excinfo.value)
    assert "not found" in log_err.text


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


async def test_helm_uninstall_does_not_error_for_release_not_found(
    log_err: LogCaptureFixture,
) -> None:
    release = Release(__file__, None, ValuesSource.none(), None)

    # Note: we haven't called install() on release.
    await release.uninstall(quiet=False)

    assert log_err.text == ""


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


async def test_helm_install_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(INSPECT_HELM_TIMEOUT, "1")
    release = Release(__file__, None, ValuesSource.none(), None)

    with pytest.raises(RuntimeError) as excinfo:
        await release.install()

    # Verify that we detect the install timeout and add our own message.
    assert "The configured timeout value was 1s. Please see the docs" in str(
        excinfo.value
    )
    # The release probably won't have been installed given the short timeout, but clean
    # up just in case.
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
        await release.install()

    mock_run.assert_called_once()
    assert (
        "--create-namespace" in mock_run.call_args[0][1]
    ) == expected_create_namespace


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
        await release.install()

    mock_run.assert_called_once()
    args = mock_run.call_args[0][1]
    assert "--set-string=sampleMetadataTestName=abc" in args
    assert "--set-string=sampleMetadataTest=5" in args


async def test_helm_install_no_extra_values() -> None:
    release = Release(__file__, None, ValuesSource.none(), None)

    with patch("k8s_sandbox._helm._run_subprocess", autospec=True) as mock_run:
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
