from textwrap import dedent

import pytest

from test.k8s_sandbox.utils import install_sandbox_environments

pytestmark = pytest.mark.req_k8s


async def test_default_entrypoint_reaps_orphans() -> None:
    script = dedent("""
        import os
        import time
        from pathlib import Path

        reader, writer = os.pipe()
        parent = os.fork()
        if parent == 0:
            os.close(reader)
            if os.fork() != 0:
                os._exit(0)

            # The parent exits first; confirm PID 1 adopts its child.
            deadline = time.monotonic() + 5
            while os.getppid() != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            os.write(writer, f"{os.getpid()} {os.getppid()}".encode())
            os._exit(0)

        os.close(writer)
        os.waitpid(parent, 0)
        with os.fdopen(reader) as stream:
            child, adopter = map(int, stream.read().split())
        assert adopter == 1, f"Child was adopted by PID {adopter}, not PID 1"

        # Only PID 1 can reap this child. A zombie still has a /proc entry.
        process = Path(f"/proc/{child}")
        deadline = time.monotonic() + 5
        while process.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not process.exists(), f"PID 1 did not reap child {child}"
    """)
    async with install_sandbox_environments(__file__, "reaper-values.yaml") as envs:
        sandbox = envs["default"]

        result = await sandbox.exec(["python", "-c", script], timeout=30)

        assert result.success, result.stdout + result.stderr
