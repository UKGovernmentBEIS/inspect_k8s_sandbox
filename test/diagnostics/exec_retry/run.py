"""E2E diagnostic for retry under real network faults.

Uses nftables REJECT/DROP rules plus ``ss --kill`` to sever the connection
between the test process and the K8s API server, then verifies that
K8sSandboxEnvironment.exec(), read_file(), and write_file() retry and recover
(or fail without retry logic). See README.md for details on the fault
injection approach.

Requires: Linux, minikube running (Docker driver), sudo, nftables.

Usage:
    sudo -n true  # verify passwordless sudo
    uv run python test/diagnostics/exec_retry/run.py
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
import kubernetes.stream.ws_client as _wsc  # type: ignore  # noqa: E402

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

import kubernetes.client.rest as _rest  # type: ignore  # noqa: E402

_orig_request = _rest.RESTClientObject.request  # type: ignore[attr-defined]


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


_rest.RESTClientObject.request = _timed_request  # type: ignore[attr-defined]
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
    assert isinstance(sandbox, K8sSandboxEnvironment)
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


async def _setup_slow_fifo(
    sandbox: K8sSandboxEnvironment,
    fifo_path: str,
    mode: str,
    cleanup_delay: int,
) -> None:
    """Set up a FIFO with a slow peer and a timed self-cleanup on the pod.

    Args:
        sandbox: The sandbox environment to set up the FIFO in.
        fifo_path: Path for the FIFO on the pod.
        mode: "read" — slow writer feeds the FIFO (~8KB/s).
              "write" — slow reader drains the FIFO (~8KB/s).
        cleanup_delay: Seconds after which the pod kills the slow peer,
            removes the FIFO, and (for read mode) replaces it with a small
            regular file. This runs entirely on the pod so it works even
            while the K8s API is blocked by nftables.
    """
    stop_file = f"{fifo_path}.stop"

    if mode == "read":
        # Slow writer: 4KB every 0.5s. The output redirection on the for
        # loop keeps the FIFO write end open across dd invocations. When
        # the reader dies (SIGPIPE), dd fails, the inner loop breaks, the
        # subshell exits, and the outer loop reopens the FIFO for the next
        # reader.
        #
        # The stop file check allows clean shutdown: when the file appears,
        # the writer exits the loop, closing the FIFO write end. The
        # retried head process sees EOF and returns cleanly. We avoid
        # pkill because it would also kill the retry's head process (which
        # has the FIFO path in its args).
        peer_cmd = (
            f'while [ ! -f {stop_file} ]; do '
            f'  while [ ! -f {stop_file} ]; do '
            '    dd if=/dev/zero bs=4096 count=1 2>/dev/null || break; '
            '    sleep 0.5; '
            f'  done > {fifo_path}; '
            'done'
        )
        cleanup_cmd = (
            f"sleep {cleanup_delay}; "
            f"touch {stop_file}"
        )
    else:
        # Slow reader: 4KB every 0.5s. The input redirection on the for
        # loop keeps the FIFO read end open across dd invocations — this
        # is critical, otherwise each dd opens/closes the FIFO and the
        # writer gets SIGPIPE immediately.
        #
        # When the stop file appears, the reader exits. The FIFO read end
        # closes, giving the pod-side head a SIGPIPE (exit 141). Tenacity
        # retries, and by then the FIFO has been removed so head writes to
        # a regular file.
        peer_cmd = (
            f'while [ ! -f {stop_file} ]; do '
            f'  while [ ! -f {stop_file} ]; do '
            '    dd of=/dev/null bs=4096 count=1 2>/dev/null || break; '
            '    sleep 0.5; '
            f'  done < {fifo_path}; '
            'done'
        )
        cleanup_cmd = (
            f"sleep {cleanup_delay}; "
            f"touch {stop_file}; "
            f"sleep 2; "
            f"rm -f {fifo_path}"
        )

    result = await sandbox.exec(
        [
            "bash",
            "-c",
            f"rm -f {stop_file}; mkfifo {fifo_path} 2>/dev/null; "
            f"({peer_cmd}) & ({cleanup_cmd}) &",
        ],
    )
    assert result.success, f"Failed to set up FIFO: {result.stderr}"


async def test_read_file_transient_fault(
    sandbox: K8sSandboxEnvironment, ip: str, port: int
) -> bool:
    """Start read_file on a slow FIFO, then block to kill the websocket.

    A slow writer feeds the FIFO at ~8KB/s, keeping the websocket open.
    After the block is lifted, a pod-side timed cleanup replaces the FIFO
    with a small regular file so the retried read_file() completes quickly.

    Timeline:
      t=0s     FIFO writer + timed cleanup started on pod
      t=1s     read_file() starts, head reads slowly from FIFO
      t=10s    block applied + ss --kill (websocket dies mid-stream)
      t=15s    block removed
      t=18s    pod-side cleanup: kills writer, replaces FIFO with regular file
      t=~20s   tenacity retry connects, reads regular file, succeeds
    """
    print(
        f"\n--- Test: read_file transient fault "
        f"(settle {SETTLE_TIME}s, block {BLOCK_DURATION}s) ---"
    )
    print("  Expected: PASS with read_file retry logic, FAIL without it")

    fifo_path = "/tmp/slow_read_fifo"
    cleanup_delay = SETTLE_TIME + BLOCK_DURATION + 15
    await _setup_slow_fifo(sandbox, fifo_path, "read", cleanup_delay)
    await asyncio.sleep(1)

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
        contents = await asyncio.wait_for(
            sandbox.read_file(fifo_path, text=False),
            timeout=120,
        )
        elapsed = time.time() - t0
        assert len(contents) > 0, "read_file returned empty"
        print(
            f"  PASS: read_file succeeded in {elapsed:.1f}s "
            f"({len(contents)} bytes)"
        )
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAIL: read_file raised {type(e).__name__} after {elapsed:.1f}s: {e}")
        return False
    finally:
        if not fault_task.done():
            fault_task.cancel()
            try:
                await fault_task
            except asyncio.CancelledError:
                pass
        _nft_cleanup()
        try:
            await sandbox.exec(
                ["bash", "-c",
                 f"touch {fifo_path}.stop; sleep 1; rm -f {fifo_path} {fifo_path}.stop"]
            )
        except Exception:
            pass


async def test_write_file_transient_fault(
    sandbox: K8sSandboxEnvironment, ip: str, port: int
) -> bool:
    """Start write_file to a slow FIFO, then block to kill the websocket.

    A slow reader drains the FIFO at ~8KB/s, so the pod-side
    ``head -c <size> > fifo`` blocks on the full pipe buffer and the
    websocket stays open. After unblocking, a pod-side timed cleanup
    removes the FIFO so the retry writes to a regular file.

    Timeline:
      t=0s     FIFO reader + timed cleanup started on pod
      t=1s     write_file() starts, sends 2MB; pod head blocks on FIFO
      t=10s    block applied + ss --kill (websocket dies mid-transfer)
      t=15s    block removed
      t=18s    pod-side cleanup: kills reader, removes FIFO
      t=~20s   tenacity retry connects, head writes to regular file, succeeds
    """
    print(
        f"\n--- Test: write_file transient fault "
        f"(settle {SETTLE_TIME}s, block {BLOCK_DURATION}s) ---"
    )
    print("  Expected: PASS with write_file retry logic, FAIL without it")

    fifo_path = "/tmp/slow_write_fifo"
    cleanup_delay = SETTLE_TIME + BLOCK_DURATION + 15
    await _setup_slow_fifo(sandbox, fifo_path, "write", cleanup_delay)
    await asyncio.sleep(1)

    # 196KB payload: large enough that the pod-side head -c takes >10s to
    # write through the slow FIFO reader (~8KB/s), keeping the websocket open
    # past SETTLE_TIME. But small enough to fit in TCP/websocket buffers so
    # _write_data_to_stdin() returns quickly and run_forever() handles the
    # recv loop — which properly detects the websocket close when ss --kill
    # fires. A 2MB payload would cause TCP backpressure, blocking write_stdin
    # indefinitely when the remote process dies.
    payload = "A" * (196 * 1024)

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
        await asyncio.wait_for(
            sandbox.write_file(fifo_path, payload),
            timeout=120,
        )
        elapsed = time.time() - t0
        print(f"  write_file succeeded in {elapsed:.1f}s, verifying...")

        result = await sandbox.exec(
            ["bash", "-c", f"wc -c < {fifo_path}"]
        )
        print(
            f"  PASS: write_file succeeded in {elapsed:.1f}s "
            f"(size on pod: {result.stdout.strip()})"
        )
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(
            f"  FAIL: write_file raised {type(e).__name__} after {elapsed:.1f}s: {e}"
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
        try:
            await sandbox.exec(
                ["bash", "-c",
                 f"touch {fifo_path}.stop; sleep 1; rm -f {fifo_path} {fifo_path}.stop"]
            )
        except Exception:
            pass


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

    _cfg = _k8s_client.Configuration.get_default_copy()  # type: ignore[attr-defined]
    print(f"kubernetes Configuration.retries = {_cfg.retries!r}")
    try:
        from k8s_sandbox._sandbox_environment import _exec_retry  # type: ignore

        print(f"tenacity _exec_retry: stop={_exec_retry.stop}, wait={_exec_retry.wait}")
    except ImportError:
        print("tenacity _exec_retry: NOT PRESENT (no retry logic)")

    results: dict[str, bool] = {}
    try:
        results["exec_transient"] = await test_transient_fault(sandbox, ip, port)
        results["exec_sustained"] = await test_sustained_fault(sandbox, ip, port)
        results["read_file_transient"] = await test_read_file_transient_fault(
            sandbox, ip, port
        )
        results["write_file_transient"] = await test_write_file_transient_fault(
            sandbox, ip, port
        )
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
