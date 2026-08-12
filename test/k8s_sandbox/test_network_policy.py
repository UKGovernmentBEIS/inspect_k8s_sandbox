import subprocess
import time
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from kubernetes import client as k8s_api  # type: ignore

from k8s_sandbox._kubernetes_api import k8s_client
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "netpol-values.yaml") as envs:
        yield envs["default"]


@pytest_asyncio.fixture(scope="module")
async def sandbox_entities_world() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "netpol-world-values.yaml"
    ) as envs:
        yield envs["default"]


async def test_allowed_fqdn(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["curl", "-I", "https://google.com"], timeout=10)

    assert result.returncode == 0


async def test_allowed_fqdn_dns_lookup(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["getent", "hosts", "google.com"], timeout=10)

    assert result.returncode == 0, result


async def test_blocked_fqdn(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["wget", "https://yahoo.com"], timeout=10)

    assert result.returncode == 4, result
    assert "Temporary failure in name resolution" in result.stderr
    # If this test is failing, it could be an issue with your cluster's Cilium
    # configuration which is not respecting the DNS rules in the egress policy.
    # E.g. you have an overly permissive egress policy that allows all DNS traffic.


async def test_blocked_fqdn_dns_lookup(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["getent", "hosts", "yahoo.com"], timeout=10)

    assert result.returncode == 2, result


async def test_allowed_cidr(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["curl", "-I", "1.1.1.1"], timeout=10)

    assert result.returncode == 0


async def test_blocked_cidr(sandbox: K8sSandboxEnvironment) -> None:
    with pytest.raises(TimeoutError):
        await sandbox.exec(["curl", "-I", "8.8.8.8"], timeout=10)


async def test_allowed_entity(sandbox_entities_world: K8sSandboxEnvironment) -> None:
    # allowEntities: ["world"]
    result = await sandbox_entities_world.exec(["curl", "-I", "yahoo.com"], timeout=10)

    assert result.returncode == 0


async def test_allowed_entity_dns_lookup(
    sandbox_entities_world: K8sSandboxEnvironment,
) -> None:
    # allowEntities: ["world"]
    result = await sandbox_entities_world.exec(
        ["getent", "hosts", "yahoo.com"], timeout=10
    )

    assert result.returncode == 0


async def test_pip_install(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(
        [
            "bash",
            "-c",
            "pip install --no-cache-dir --no-input requests > /dev/null 2>&1 && "
            "echo 'success' || echo 'failed'",
        ],
        # Test occasionally failed with TimeoutError when timeout is set to 10
        timeout=30,
    )

    assert result.stdout.strip() == "success"


async def test_ingress_from_outside_sandbox_is_denied(
    sandbox: K8sSandboxEnvironment,
) -> None:
    """Traffic from a pod outside the sandbox is dropped by default-deny-ingress.

    Guards against the `-sandbox-default-deny-ingress` policy being (or
    becoming) allow-all — the concern raised in #228. Note that in
    CiliumNetworkPolicies an empty ingress rule (`- {}`) puts the endpoint
    into ingress default-deny while whitelisting nothing, unlike k8s
    NetworkPolicies where it means allow-all.
    """
    pod_info = sandbox._pod.info
    api = k8s_client(pod_info.context_name)

    # Serve HTTP from the sandbox pod; prove the listener is up via localhost.
    result = await sandbox.exec(
        ["sh", "-c", "nohup python3 -m http.server 8080 >/tmp/http.log 2>&1 &"],
        timeout=10,
    )
    assert result.returncode == 0, result
    result = await sandbox.exec(
        [
            "sh",
            "-c",
            "for i in $(seq 10); do"
            " curl -s -o /dev/null -m 2 http://127.0.0.1:8080/ && exit 0; sleep 1;"
            " done; exit 1",
        ],
        timeout=30,
    )
    assert result.returncode == 0, f"listener did not come up: {result}"

    pod = api.read_namespaced_pod(pod_info.name, pod_info.namespace)
    assert pod.status is not None
    pod_ip = pod.status.pod_ip

    attacker = "test-ingress-attacker"
    _delete_pod_if_exists(api, attacker, pod_info.namespace)
    api.create_namespaced_pod(
        pod_info.namespace,
        k8s_api.V1Pod(
            metadata=k8s_api.V1ObjectMeta(name=attacker),
            spec=k8s_api.V1PodSpec(
                containers=[
                    k8s_api.V1Container(
                        name="curl",
                        image="curlimages/curl",
                        command=["sleep", "600"],
                    )
                ],
                restart_policy="Never",
            ),
        ),
    )
    try:
        _wait_for_pod_ready(api, attacker, pod_info.namespace)
        context_args = (
            ["--context", pod_info.context_name] if pod_info.context_name else []
        )
        curl = subprocess.run(
            ["kubectl", "exec", attacker, "-n", pod_info.namespace, *context_args]
            + ["--", "curl", "-sS", "-m", "10", f"http://{pod_ip}:8080/"],
            capture_output=True,
            text=True,
        )
        # 28 is curl's timeout: the SYN was dropped by the ingress policy. Anything
        # else (200, or 7/refused) means the attacker's traffic reached the pod.
        assert curl.returncode == 28, (
            f"expected ingress to be dropped, got rc={curl.returncode} "
            f"stdout={curl.stdout!r} stderr={curl.stderr!r}"
        )
    finally:
        _delete_pod_if_exists(api, attacker, pod_info.namespace)


def _delete_pod_if_exists(api: k8s_api.CoreV1Api, name: str, namespace: str) -> None:
    try:
        api.delete_namespaced_pod(name, namespace)
    except k8s_api.exceptions.ApiException as e:
        if e.status != 404:
            raise


def _wait_for_pod_ready(
    api: k8s_api.CoreV1Api, name: str, namespace: str, timeout: int = 120
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod = api.read_namespaced_pod(name, namespace)
        conditions = (pod.status.conditions if pod.status else None) or []
        if any(c.type == "Ready" and c.status == "True" for c in conditions):
            return
        time.sleep(1)
    raise TimeoutError(f"pod {name} did not become ready within {timeout}s")
