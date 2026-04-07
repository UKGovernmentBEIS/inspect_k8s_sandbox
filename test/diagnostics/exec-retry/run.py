"""E2E diagnostic for exec retry under real network faults.

SLOP ALERT: Entire program written by Claude

Uses nftables REJECT/DROP rules plus ``ss --kill`` to sever the connection
between the test process and the K8s API server, then verifies that
K8sSandboxEnvironment.exec() retries and recovers (or fails without retry
logic). See README.md for details on the fault injection approach.

Requires: Linux, minikube running (Docker driver), sudo, nftables.

Usage:
    sudo -n true  # verify passwordless sudo
    uv run python test/diagnostics/exec-retry/run.py
"""

import asyncio
import functools
import logging
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.WARNING,
    format="%(relativeCreated)6.0fms %(name)s %(message)s",
)

# ---- Debug instrumentation ----
import kubernetes.stream.ws_client as _wsc  # noqa: E402

_orig_websocket_call = _wsc.websocket_call


@functools.wraps(_orig_websocket_call)
def _timed_websocket_call(*args, **kwargs):
    t0 = time.time()
    try:
        result = _orig_websocket_call(*args, **kwargs)
        print(f"  [ws] websocket_call ok in {time.time() - t0:.1f}s")
        return result
    except Exception as e:
        print(
            f"  [ws] websocket_call raised {type(e).__name__} "
            f"in {time.time() - t0:.1f}s: {e}"
        )
        raise


_wsc.websocket_call = _timed_websocket_call

import kubernetes.client.rest as _rest  # noqa: E402

_orig_request = _rest.RESTClientObject.request


@functools.wraps(_orig_request)
def _timed_request(self, method, url, **kwargs):
    short_url = url.split("?")[0] if url else url
    t0 = time.time()
    try:
        result = _orig_request(self, method, url, **kwargs)
        print(f"  [rest] {method} {short_url} ok in {time.time() - t0:.1f}s")
        return result
    except Exception as e:
        print(
            f"  [rest] {method} {short_url} raised {type(e).__name__} "
            f"in {time.time() - t0:.1f}s"
        )
        raise


_rest.RESTClientObject.request = _timed_request
# ---- End debug instrumentation ----

from k8s_sandbox._sandbox_environment import (  # noqa: E402
    K8sError,
    K8sSandboxEnvironment,
    K8sSandboxEnvironmentConfig,
)

TABLE_NAME = "exec_retry_test"

# Time to wait after starting exec before applying the block.
# This lets _check_for_pod_restart (REST call) and the websocket handshake
# complete, so the block hits an established websocket mid-stream.
SETTLE_TIME = 10

# Duration of the transient block. With ss --kill, the connection dies
# immediately. Keep the block for a few seconds to ensure the error
# propagates before unblocking for the retry.
BLOCK_DURATION = 5


def _nft(rule: str) -> None:
    subprocess.run(["sudo", "nft", rule], check=True, capture_output=True)


def _nft_block(ip: str, port: int) -> None:
    _nft(f"add table inet {TABLE_NAME}")
    # Block outgoing packets — REJECT sends RST/ICMP so local sockets get
    # an immediate error when they try to send.
    _nft(
        f"add chain inet {TABLE_NAME} output {{ type filter hook output priority 0 ; }}"
    )
    _nft(f"add rule inet {TABLE_NAME} output ip daddr {ip} tcp dport {port} reject")
    # Block incoming packets — DROP silently discards server responses so
    # reads on established connections hang (then fail when the local side
    # tries to ACK and hits the output REJECT).
    _nft(f"add chain inet {TABLE_NAME} input {{ type filter hook input priority 0 ; }}")
    _nft(f"add rule inet {TABLE_NAME} input ip saddr {ip} tcp sport {port} drop")
    # Kill existing TCP connections using ss. The nftables rules only affect
    # new packets — established connections with data in flight may survive
    # until a send/recv hits the firewall. Force-closing with ss ensures
    # the local socket gets an immediate error.
    subprocess.run(
        [
            "sudo",
            "ss",
            "--kill",
            "state",
            "established",
            f"dst {ip}",
            f"dport = {port}",
        ],
        capture_output=True,
    )


def _nft_unblock() -> None:
    _nft(f"delete table inet {TABLE_NAME}")


def _nft_cleanup() -> None:
    try:
        _nft_unblock()
    except subprocess.CalledProcessError:
        pass


@contextmanager
def nft_block(ip: str, port: int):
    _nft_block(ip, port)
    try:
        yield
    finally:
        _nft_unblock()


def get_api_server_address() -> tuple[str, int]:
    result = subprocess.run(
        [
            "kubectl",
            "config",
            "view",
            "--minify",
            "-o",
            "jsonpath={.clusters[0].cluster.server}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = urlparse(result.stdout.strip())
    host = parsed.hostname
    port = parsed.port or 443
    assert host is not None, "Could not parse API server host from kubeconfig"
    return (host, port)


def assert_block_effective() -> None:
    probe = subprocess.run(
        ["kubectl", "get", "pods", "--request-timeout=2s"],
        capture_output=True,
        timeout=5,
    )
    assert probe.returncode != 0, "nftables block not effective"
    print("  Block verified")


def check_prerequisites() -> None:
    r = subprocess.run(["sudo", "-n", "true"], capture_output=True)
    if r.returncode != 0:
        sys.exit("FAIL: passwordless sudo not available")
    r = subprocess.run(["which", "nft"], capture_output=True)
    if r.returncode != 0:
        sys.exit("FAIL: nft not found")
    r = subprocess.run(["minikube", "status"], capture_output=True)
    if r.returncode != 0:
        sys.exit("FAIL: minikube not running")
    print("Prerequisites OK")


async def install_sandbox() -> tuple[K8sSandboxEnvironment, dict]:
    values_path = Path(__file__).parent / "values.yaml"
    config = K8sSandboxEnvironmentConfig(values=values_path)
    envs = await K8sSandboxEnvironment.sample_init("exec-retry-diag", config, {})
    sandbox = next(iter(envs.values()))
    result = await sandbox.exec(["echo", "ready"])
    assert result.success, "Sandbox not healthy after install"
    return sandbox, envs


async def test_transient_fault(
    sandbox: K8sSandboxEnvironment, ip: str, port: int
) -> bool:
    """Start a long-running exec, then briefly block to kill the websocket.

    With retry logic, the exec retries after the block is lifted and
    succeeds. Without retry, it fails.

    Timeline:
      t=0s    exec(["sleep 5 && echo hello"]) starts
      t=10s   REST check done, websocket established, sleep running
      t=10s   block applied + ss --kill (websocket dies immediately)
      t=15s   block removed
      t=15+s  tenacity retries → new exec → sleep 5 → echo hello
      ~t=25s  exec succeeds
    """
    print(
        f"\n--- Test: transient fault "
        f"(settle {SETTLE_TIME}s, block {BLOCK_DURATION}s) ---"
    )
    print("  Expected: PASS with exec retry logic, FAIL without it")

    t0 = time.time()

    async def block_then_unblock():
        await asyncio.sleep(SETTLE_TIME)
        print(f"  Applying block + ss --kill at t={time.time() - t0:.1f}s")
        _nft_block(ip, port)
        assert_block_effective()
        await asyncio.sleep(BLOCK_DURATION)
        _nft_unblock()
        print(f"  Unblocked at t={time.time() - t0:.1f}s")

    fault_task = asyncio.create_task(block_then_unblock())

    try:
        # Sleep must outlast SETTLE_TIME so the websocket is still active
        # when the block + ss --kill fires.
        result = await sandbox.exec(
            ["bash", "-c", "sleep 30 && echo hello"],
            timeout=120,
        )
        elapsed = time.time() - t0
        assert result.success
        assert "hello" in result.stdout
        print(f"  PASS: exec succeeded in {elapsed:.1f}s")
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAIL: exec raised {type(e).__name__} after {elapsed:.1f}s")
        return False
    finally:
        if not fault_task.done():
            fault_task.cancel()
            try:
                await fault_task
            except asyncio.CancelledError:
                pass
        _nft_cleanup()


async def test_sustained_fault(
    sandbox: K8sSandboxEnvironment, ip: str, port: int
) -> bool:
    """Start a long-running exec, then block permanently.

    The websocket dies and the exec should raise K8sError.
    """
    print("\n--- Test: sustained fault (block after websocket established) ---")
    print("  Expected: PASS (K8sError raised)")

    t0 = time.time()

    async def block_after_settle():
        await asyncio.sleep(SETTLE_TIME)
        print(f"  Applying permanent block at t={time.time() - t0:.1f}s")
        _nft_block(ip, port)
        assert_block_effective()

    fault_task = asyncio.create_task(block_after_settle())

    try:
        await sandbox.exec(
            ["bash", "-c", "sleep 900"],
            timeout=60,
        )
        elapsed = time.time() - t0
        print(f"  FAIL: exec succeeded after {elapsed:.1f}s (should have raised)")
        return False
    except K8sError:
        elapsed = time.time() - t0
        print(f"  PASS: K8sError raised after {elapsed:.1f}s")
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(
            f"  FAIL: raised {type(e).__name__} "
            f"(expected K8sError) after {elapsed:.1f}s"
        )
        return False
    finally:
        if not fault_task.done():
            fault_task.cancel()
            try:
                await fault_task
            except asyncio.CancelledError:
                pass
        _nft_cleanup()


async def test_recovery(sandbox: K8sSandboxEnvironment) -> bool:
    """Verify sandbox still works after fault tests."""
    print("\n--- Test: recovery ---")
    # Brief pause to let connection pools settle after sustained block
    await asyncio.sleep(2)
    result = await sandbox.exec(["echo", "recovered"])
    assert result.success
    assert "recovered" in result.stdout
    print(f"  PASS: exec succeeded (stdout={result.stdout.strip()!r})")
    return True


async def main() -> None:
    check_prerequisites()

    ip, port = get_api_server_address()
    print(f"API server: {ip}:{port}")

    print("\nInstalling sandbox...")
    sandbox, envs = await install_sandbox()
    print("Sandbox ready")

    # Report config
    from kubernetes import client as _k8s_client

    _cfg = _k8s_client.Configuration.get_default_copy()
    print(f"kubernetes Configuration.retries = {_cfg.retries!r}")
    try:
        from k8s_sandbox._sandbox_environment import _exec_retry  # type: ignore

        print(f"tenacity _exec_retry: stop={_exec_retry.stop}, wait={_exec_retry.wait}")
    except ImportError:
        print("tenacity _exec_retry: NOT PRESENT (no retry logic)")

    results: dict[str, bool] = {}
    try:
        results["transient"] = await test_transient_fault(sandbox, ip, port)
        results["sustained"] = await test_sustained_fault(sandbox, ip, port)
        results["recovery"] = await test_recovery(sandbox)
    finally:
        _nft_cleanup()
        try:
            await sandbox.release.uninstall(quiet=True)
        except Exception:
            pass

    print("\n--- Summary ---")
    for name, passed in results.items():
        print(f"  {name}: {'PASS' if passed else 'FAIL'}")

    all_pass = all(results.values())
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
