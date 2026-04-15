"""Tests for Kubernetes API configuration loading."""

import importlib
import time
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
        from kubernetes.config import ConfigException  # type: ignore

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


class TestInspectK8sDefaultNamespace:
    """Tests for INSPECT_K8S_DEFAULT_NAMESPACE env var override."""

    def test_env_var_overrides_incluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env var takes precedence over in-cluster namespace."""
        monkeypatch.setenv("INSPECT_K8S_DEFAULT_NAMESPACE", "sandbox-ns")
        assert get_default_namespace(context_name=None) == "sandbox-ns"

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_env_var_overrides_kubeconfig(
        self, mock_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env var takes precedence over kubeconfig context namespace."""
        from kubernetes.config import ConfigException

        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.side_effect = ConfigException()
        typed_config.load_kube_config.return_value = None
        typed_config.list_kube_config_contexts.return_value = (
            [{"name": "test", "context": {"namespace": "kubeconfig-ns"}}],
            {"name": "test", "context": {"namespace": "kubeconfig-ns"}},
        )
        monkeypatch.setenv("INSPECT_K8S_DEFAULT_NAMESPACE", "override-ns")

        assert get_default_namespace(context_name=None) == "override-ns"

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_unset_falls_through(
        self, mock_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When unset, existing behavior is preserved."""
        from kubernetes.config import ConfigException

        monkeypatch.delenv("INSPECT_K8S_DEFAULT_NAMESPACE", raising=False)
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.side_effect = ConfigException()
        typed_config.load_kube_config.return_value = None
        typed_config.list_kube_config_contexts.return_value = (
            [{"name": "test", "context": {"namespace": "kubeconfig-ns"}}],
            {"name": "test", "context": {"namespace": "kubeconfig-ns"}},
        )

        assert get_default_namespace(context_name=None) == "kubeconfig-ns"

    @patch("k8s_sandbox._kubernetes_api.config")
    def test_empty_string_falls_through(
        self, mock_config: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty string is treated as unset."""
        from kubernetes.config import ConfigException

        monkeypatch.setenv("INSPECT_K8S_DEFAULT_NAMESPACE", "")
        typed_config = cast(_ConfigMock, mock_config)
        typed_config.load_incluster_config.side_effect = ConfigException()
        typed_config.load_kube_config.return_value = None
        typed_config.list_kube_config_contexts.return_value = (
            [{"name": "test", "context": {"namespace": "kubeconfig-ns"}}],
            {"name": "test", "context": {"namespace": "kubeconfig-ns"}},
        )

        assert get_default_namespace(context_name=None) == "kubeconfig-ns"


_get_client_refresh_seconds = getattr(_KUBE_API, "_get_client_refresh_seconds")


class TestClientRefreshSeconds:
    """Tests for _get_client_refresh_seconds env var parsing."""

    def test_unset_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INSPECT_K8S_CLIENT_REFRESH_SECONDS", raising=False)
        assert _get_client_refresh_seconds() == 0

    def test_zero_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSPECT_K8S_CLIENT_REFRESH_SECONDS", "0")
        assert _get_client_refresh_seconds() == 0

    def test_positive_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSPECT_K8S_CLIENT_REFRESH_SECONDS", "600")
        assert _get_client_refresh_seconds() == 600

    def test_negative_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSPECT_K8S_CLIENT_REFRESH_SECONDS", "-1")
        with pytest.raises(ValueError, match="must be a non-negative int"):
            _get_client_refresh_seconds()

    def test_non_integer_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSPECT_K8S_CLIENT_REFRESH_SECONDS", "abc")
        with pytest.raises(ValueError, match="must be a non-negative int"):
            _get_client_refresh_seconds()


_ThreadLocalClientFactory = getattr(_KUBE_API, "_ThreadLocalClientFactory")


class TestClientRefresh:
    """Tests for _ThreadLocalClientFactory client refresh behavior."""

    @patch("k8s_sandbox._kubernetes_api.config")
    @patch("k8s_sandbox._kubernetes_api._get_client_refresh_seconds", return_value=0)
    def test_client_cached_when_refresh_disabled(
        self, _mock_refresh: MagicMock, mock_config: MagicMock
    ) -> None:
        """When refresh is disabled, the same client is returned every time."""
        mock_config.new_client_from_config.return_value = MagicMock()
        factory = _ThreadLocalClientFactory()
        client1 = factory.get_client("ctx")
        client2 = factory.get_client("ctx")
        assert client1 is client2
        mock_config.new_client_from_config.assert_called_once()

    @patch("k8s_sandbox._kubernetes_api.config")
    @patch("k8s_sandbox._kubernetes_api._get_client_refresh_seconds", return_value=1)
    def test_client_refreshed_when_expired(
        self, _mock_refresh: MagicMock, mock_config: MagicMock
    ) -> None:
        """When refresh is enabled and client is stale, a new client is created."""
        mock_api_client_1 = MagicMock()
        mock_api_client_2 = MagicMock()
        mock_config.new_client_from_config.side_effect = [
            mock_api_client_1,
            mock_api_client_2,
        ]
        factory = _ThreadLocalClientFactory()
        client1 = factory.get_client("ctx")

        # Backdate the creation time so it appears expired.
        factory._created_at["ctx"] = time.monotonic() - 2

        client2 = factory.get_client("ctx")
        assert client1 is not client2
        assert mock_config.new_client_from_config.call_count == 2
        mock_api_client_1.close.assert_called_once()

    @patch("k8s_sandbox._kubernetes_api.config")
    @patch("k8s_sandbox._kubernetes_api._get_client_refresh_seconds", return_value=1)
    def test_client_not_refreshed_when_young(
        self, _mock_refresh: MagicMock, mock_config: MagicMock
    ) -> None:
        """When refresh is enabled but client is young, cached client is returned."""
        mock_config.new_client_from_config.return_value = MagicMock()
        factory = _ThreadLocalClientFactory()
        client1 = factory.get_client("ctx")
        client2 = factory.get_client("ctx")
        assert client1 is client2
        mock_config.new_client_from_config.assert_called_once()

    @patch("k8s_sandbox._kubernetes_api.config")
    @patch("k8s_sandbox._kubernetes_api._get_client_refresh_seconds", return_value=1)
    def test_current_context_client_refreshed(
        self, _mock_refresh: MagicMock, mock_config: MagicMock
    ) -> None:
        """Current-context client (context_name=None) is also refreshed."""
        Config._instance = Config(contexts=None, current_context=None, in_cluster=False)  # type: ignore[call-arg]
        mock_api_client_1 = MagicMock()
        mock_api_client_2 = MagicMock()
        mock_config.new_client_from_config.side_effect = [
            mock_api_client_1,
            mock_api_client_2,
        ]
        factory = _ThreadLocalClientFactory()
        client1 = factory.get_client(None)

        factory._created_at[None] = time.monotonic() - 2

        client2 = factory.get_client(None)
        assert client1 is not client2
        assert mock_config.new_client_from_config.call_count == 2
        mock_api_client_1.close.assert_called_once()

    @patch("k8s_sandbox._kubernetes_api.client")
    @patch("k8s_sandbox._kubernetes_api.config")
    @patch("k8s_sandbox._kubernetes_api._get_client_refresh_seconds", return_value=0)
    def test_incluster_uses_default_client(
        self, _mock_refresh: MagicMock, mock_config: MagicMock, mock_client: MagicMock
    ) -> None:
        """In-cluster mode uses client.CoreV1Api() (built-in token refresh)."""
        Config._instance = Config(contexts=None, current_context=None, in_cluster=True)  # type: ignore[call-arg]
        factory = _ThreadLocalClientFactory()
        factory.get_client(None)
        mock_client.CoreV1Api.assert_called_once_with()
        mock_config.new_client_from_config.assert_not_called()
