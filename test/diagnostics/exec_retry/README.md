# exec-retry diagnostic

E2E test for `K8sSandboxEnvironment` retry logic under real network faults,
using nftables to block the K8s API server. Covers `exec()`, `read_file()`,
and `write_file()`.

## Prerequisites

- Linux with minikube running (Docker driver)
- `nft` available (nftables package)
- Passwordless `sudo`
- This repo cloned with `uv sync --extra dev`

Designed for AISI dev VMs. Will not work on macOS or in CI.

## What it tests

1. **exec transient fault**: blocks the API server mid-exec, then unblocks
   after `BLOCK_DURATION` seconds. With tenacity retry, exec should recover on
   the next attempt. Without retry logic, exec fails.

2. **exec sustained fault**: blocks the API server for the entire exec attempt.
   Expects `K8sError` (with or without retry logic).

3. **read_file transient fault**: reads from a FIFO with a slow writer
   (~8KB/s) so the websocket stays open long enough to inject the fault.
   After unblocking, the FIFO writer is killed so the retried read gets
   EOF quickly.

4. **write_file transient fault**: writes to a FIFO with a slow reader
   (~8KB/s). The pod-side `head -c <size> > fifo` blocks on the full
   pipe buffer, keeping the websocket open. After unblocking, the FIFO
   is replaced with a regular file path so the retry completes quickly.

5. **Recovery**: confirms the sandbox still works after faults are cleared.

## How it works

### Network topology

minikube with the Docker driver runs the K8s API server inside a Docker
container. It's exposed on the Docker bridge (e.g. `192.168.49.2:8443`).
Traffic from the host traverses the OUTPUT chain, so nftables rules work.

### Fault injection

Three mechanisms work together to kill connections immediately:

1. **nftables OUTPUT REJECT** — new outgoing TCP packets to the API server
   get an immediate RST/ICMP unreachable, causing `ConnectionRefusedError`.
2. **nftables INPUT DROP** — silently discards server responses so reads on
   established connections stall (then fail when the local side tries to ACK
   and hits the output REJECT).
3. **`ss --kill`** — force-closes existing TCP connections so the local socket
   gets an immediate error rather than waiting for the next send/recv to hit
   the firewall.

### Why nftables and not toxiproxy / tc netem?

- **nftables** is pre-installed on modern Linux. No extra dependencies, and
  gives binary pass/fail control.
- **toxiproxy** would require installing a binary, creating a proxy, and
  pointing kubeconfig at a different address. More moving parts.
- **tc netem** adds latency/jitter/loss but doesn't cleanly simulate a
  connection refused or reset. The retry logic triggers on `ApiException`,
  `WebSocketException`, `ConnectionError`, `OSError` — not on slow responses.

### Timing: SETTLE_TIME

The test starts a long-running `exec()`, then waits `SETTLE_TIME` seconds
before applying the block. This lets `check_for_pod_restart()` (a REST API
call outside the tenacity retry loop) and the WebSocket handshake complete,
so the fault hits an established WebSocket mid-stream. If the block were
applied immediately, it would hit the REST call and raise `K8sError` before
the retry loop even starts — which is not what we're testing.

This is a time-based heuristic and could be fragile on a slow cluster. An
alternative would be to monkeypatch `check_for_pod_restart` to inject the
fault at the right moment (as explored in the original spec), but that's
harder in a standalone script.

### Slow FIFOs for read_file / write_file

`read_file()` and `write_file()` don't run long-lived commands like `exec()`
does with `sleep 30`. To keep their websocket open long enough for the fault,
the tests use a named pipe (FIFO) with a slow peer:

- **read_file**: a background process slowly writes ~8KB/s into the FIFO.
  `head -c <limit> /tmp/fifo` blocks reading, keeping the websocket alive.
- **write_file**: a background process slowly reads ~8KB/s from the FIFO.
  The pod-side `head -c <size> > /tmp/fifo` blocks after filling the 64KB
  pipe buffer.

After the fault, the retry needs to complete quickly. A **pod-side timed
cleanup** (scheduled via `sleep <N> && pkill ... && rm ...` before the test
starts) replaces the FIFO with a regular file (for read) or removes it
(for write, so the shell redirection creates a regular file). This runs
entirely on the pod, so it works even while nftables blocks the K8s API.

## What this doesn't test

- **Mid-stream disconnection on an idle WebSocket**: REJECT blocks new
  packets, and `ss --kill` closes sockets, but doesn't replicate the exact
  `BrokenPipeError` path from a half-closed TCP connection. To test that
  you'd need `conntrack`-based connection killing.
- **API server 503s**: REJECT produces `ConnectionRefusedError`, not
  `ApiException(status=503)`. Simulating 503s would require a proxy.
  The retry logic treats both as transient.
- **Helm install/uninstall under faults**: not tested. The sandbox is
  installed before faults begin.

## Safety

- Rules live in a dedicated nftables table (`exec_retry_test`) — cleanup
  can't affect other tables.
- Only traffic to the API server IP:port is affected. DNS, SSH, internet
  access continue normally.
- `finally` blocks ensure cleanup even on failure.
- If the script crashes mid-run: `sudo nft delete table inet exec_retry_test`

## Running

```bash
sudo -n true  # verify passwordless sudo works
uv run python test/diagnostics/exec_retry/run.py
```

Expected results:
- **With retry on exec, read_file, write_file**: all 5 tests PASS
- **With retry on exec only**: exec tests PASS, read_file/write_file FAIL, recovery PASS
- **Without retry logic**: exec_transient FAIL, read_file/write_file FAIL, exec_sustained PASS, recovery PASS
