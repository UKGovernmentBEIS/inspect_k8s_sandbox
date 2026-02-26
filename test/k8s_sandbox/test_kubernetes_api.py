"""Tests for Kubernetes API configuration loading."""

import importlib
from pathlib import Path
from typing import Iterator, Protocol, Self, cast
from unittest.mock import MagicMock, patch

import pytest

from k8s_sandbox._kubernetes_api import get_default_namespace

_KUBE_API = importlib.import_module("k8s_sandbox._kubernetes_api")


class _ConfigProtocol(Protocol):
    _instance: object | None
    in_cluster: bool

    @classmethod
    def get_instance(cls) -> Self: ...

    def get_context(self, context_name: str | None) -> dict[str, object]: ...


class _ConfigMock(Protocol):
    load_incluster_config: MagicMock
    load_kube_config: MagicMock
    list_kube_config_contexts: MagicMock


Config = cast(type[_ConfigProtocol], getattr(_KUBE_API, "_Config"))


def _reset_config_instance() -> None:
    setattr(Config, "_instance", None)


@pytest.fixture(autouse=True)
def _reset_config() -> Iterator[None]:
    """Reset the _Config singleton before and after every test.

    Without the post-test reset, the singleton leaks mocked state into
    downstream integration tests (e.g. test_network_policy) that rely on
    the real kubeconfig being loaded.
    """
    _reset_config_instance()
    yield
    _reset_config_instance()


class TestConfigLoading:
    """Tests for _Config loading with in-cluster and kubeconfig fallback."""

    # Singleton reset handled by _reset_config autouse fixture.

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_loads_incluster_config_when_available(
        self, mock_config: MagicMock
    ) -> None:
        """When running in a pod, load_incluster_config() is used."""
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.return_value = None

        instance = Config.get_instance()

        typed_config.load_incluster_config.assert_called_once()
        typed_config.load_kube_config.assert_not_called()
        assert instance.in_cluster is True

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_falls_back_to_kubeconfig(self, mock_config: MagicMock) -> None:
        """When not in a pod, falls back to load_kube_config()."""
        from kubernetes.config import ConfigException

        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.side_effect = ConfigException(
            "not in cluster"
        )
        typed_config.load_kube_config.return_value = None
        typed_config.list_kube_config_contexts.return_value = (
            [{"name": "test", "context": {"namespace": "default"}}],
            {"name": "test", "context": {"namespace": "default"}},
        )

        instance = Config.get_instance()

        typed_config.load_incluster_config.assert_called_once()
        typed_config.load_kube_config.assert_called_once()
        assert instance.in_cluster is False

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_incluster_get_context_returns_none_context_name(
        self, mock_config: MagicMock
    ) -> None:
        """In-cluster mode: get_context(None) returns a synthetic context."""
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.return_value = None

        instance = Config.get_instance()
        context = instance.get_context(None)

        assert context["name"] == "in-cluster"

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_incluster_rejects_named_context(self, mock_config: MagicMock) -> None:
        """In-cluster mode: get_context('some-name') raises ValueError."""
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.return_value = None

        instance = Config.get_instance()

        with pytest.raises(ValueError, match="Named contexts are not available"):
            _ = instance.get_context("some-context")


class TestGetDefaultNamespace:
    """Tests for namespace resolution in both modes."""

    # Singleton reset handled by _reset_config autouse fixture.

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_incluster_reads_namespace_from_sa_token(
        self, mock_config: MagicMock, tmp_path: Path
    ) -> None:
        """In-cluster mode reads namespace from the SA token mount."""
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.return_value = None

        ns_file = tmp_path / "namespace"
        _ = ns_file.write_text("researcher")

        instance = Config.get_instance()
        setattr(Config, "_instance", instance)

        with patch(
            "k8s_sandbox._kubernetes_api._INCLUSTER_NAMESPACE_PATH", str(ns_file)
        ):
            namespace = get_default_namespace(context_name=None)

        assert namespace == "researcher"

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_incluster_defaults_to_default_namespace(
        self, mock_config: MagicMock
    ) -> None:
        """In-cluster mode defaults to 'default' if SA namespace file is missing."""
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.return_value = None

        instance = Config.get_instance()
        setattr(Config, "_instance", instance)

        with patch(
            "k8s_sandbox._kubernetes_api._INCLUSTER_NAMESPACE_PATH",
            "/nonexistent/path",
        ):
            namespace = get_default_namespace(context_name=None)

        assert namespace == "default"

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_kubeconfig_reads_namespace_from_context(
        self, mock_config: MagicMock
    ) -> None:
        """Kubeconfig mode reads namespace from the context (existing behavior)."""
        from kubernetes.config import ConfigException

        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.side_effect = ConfigException()
        typed_config.load_kube_config.return_value = None
        typed_config.list_kube_config_contexts.return_value = (
            [{"name": "test", "context": {"namespace": "my-namespace"}}],
            {"name": "test", "context": {"namespace": "my-namespace"}},
        )

        namespace = get_default_namespace(context_name=None)

        assert namespace == "my-namespace"
