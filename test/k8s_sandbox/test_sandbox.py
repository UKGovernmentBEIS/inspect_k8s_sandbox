import asyncio
import logging
import os
import re
from textwrap import dedent
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from inspect_ai.util import OutputLimitExceededError, SandboxEnvironmentLimits
from kubernetes.stream.ws_client import ApiException, WSClient  # type: ignore
from pytest import LogCaptureFixture

from k8s_sandbox._kubernetes_api import get_current_context_name, k8s_client
from k8s_sandbox._sandbox_environment import K8sError, K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandboxes() -> AsyncGenerator[dict[str, K8sSandboxEnvironment], None]:
    async with install_sandbox_environments(__file__, "values.yaml") as envs:
        yield envs


@pytest_asyncio.fixture(scope="module")
async def sandbox(sandboxes: dict[str, K8sSandboxEnvironment]) -> K8sSandboxEnvironment:
    default_sandbox = sandboxes["default"]
    cwd = (await default_sandbox.exec(["pwd"])).stdout.strip()
    assert cwd == "/root", (
        "Some tests assume that the pod's cwd is /root. "
        f"From running `pwd`, it appears to be '{cwd}'."
    )
    return default_sandbox


@pytest_asyncio.fixture(scope="module")
async def sandbox_non_root(
    sandboxes: dict[str, K8sSandboxEnvironment],
) -> K8sSandboxEnvironment:
    return sandboxes["nonroot"]


@pytest_asyncio.fixture(scope="module")
async def sandbox_busybox(
    sandboxes: dict[str, K8sSandboxEnvironment],
) -> K8sSandboxEnvironment:
    return sandboxes["busybox"]


@pytest_asyncio.fixture(scope="module")
async def sandbox_with_default_user() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "default-user-values.yaml", default_user="ubuntu"
    ) as envs:
        yield envs["default"]


@pytest.fixture
def binary_data() -> bytes:
    return bytes(range(256))


@pytest.fixture
def log_err(caplog: LogCaptureFixture) -> LogCaptureFixture:
    # Note: this will prevent lower level messages from being shown in pytest output.
    caplog.set_level(logging.ERROR)
    return caplog


@pytest.fixture
def log_warning(caplog: LogCaptureFixture) -> LogCaptureFixture:
    # Note: this will prevent lower level messages from being shown in pytest output.
    caplog.set_level(logging.WARNING)
    return caplog


### exec() ###


@pytest.mark.parametrize(
    "cmd", [["echo", "Hello, World!"], ["bash", "-c", "echo Hello, World!"]]
)
async def test_exec_with_success(
    sandbox: K8sSandboxEnvironment, cmd: list[str]
) -> None:
    result = await sandbox.exec(cmd)

    assert result.success
    assert result.returncode == 0
    assert result.stdout.strip() == "Hello, World!"
    assert result.stderr == ""


async def test_exec_with_error(sandbox: K8sSandboxEnvironment) -> None:
    # sudo is not installed in the container.
    result = await sandbox.exec(["sudo"])

    assert not result.success
    assert result.returncode == 127
    assert result.stdout == ""
    assert "sudo: not found" in result.stderr.casefold()


async def test_exec_with_error_via_bash(sandbox: K8sSandboxEnvironment) -> None:
    # sudo is not installed in the container.
    result = await sandbox.exec(["bash", "-c", "sudo"])

    assert not result.success
    assert result.returncode == 127
    assert result.stdout == ""
    assert "command not found" in result.stderr.casefold()


async def test_exec_flushes_stderr(sandbox: K8sSandboxEnvironment) -> None:
    head_limit = 1024  # 1 KiB

    result = await sandbox.exec(["sh", "-c", f"yes | head -c {head_limit} 1>&2"])

    assert result.success
    assert len(result.stderr) == head_limit


async def test_exec_stdin_as_text(sandbox: K8sSandboxEnvironment) -> None:
    cmd = """read value && echo "Echoed from stdin: $value" """

    result = await sandbox.exec(["bash", "-c", cmd], input="success\n", timeout=5)

    assert result.success
    assert result.returncode == 0
    assert "Echoed from stdin: success" in result.stdout
    assert result.stderr == ""


async def test_exec_stdin_with_newlines(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(
        ["bash", "-c", "cat"], input="some\nnew\nlines", timeout=5
    )

    assert result.success
    assert result.returncode == 0
    assert "some\nnew\nlines" in result.stdout
    assert result.stderr == ""


async def test_exec_stdin_empty(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["bash", "-c", "cat"], input="", timeout=5)

    assert result.success
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


async def test_exec_stdin_requiring_quotes(sandbox: K8sSandboxEnvironment) -> None:
    stdin = "cmd_injection'\";\n\n 😀 exit 1"

    result = await sandbox.exec(["bash", "-c", "cat"], input=stdin, timeout=5)

    assert result.success
    assert result.returncode == 0
    assert result.stdout == stdin
    assert result.stderr == ""


async def test_exec_stdin_as_bytes(
    sandbox: K8sSandboxEnvironment, binary_data: bytes
) -> None:
    await sandbox.exec(["bash", "-c", "cat > file.bin"], input=binary_data, timeout=5)

    actual = await sandbox.read_file("file.bin", text=False)
    assert actual == binary_data


async def test_exec_cwd_absolute(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["pwd"], cwd="/tmp", timeout=10)

    assert result.success
    assert result.stdout.strip() == "/tmp"


async def test_exec_cwd_relative(sandbox: K8sSandboxEnvironment) -> None:
    await sandbox.exec(["mkdir", "-p", "/root/exec-cwd"])

    result = await sandbox.exec(["pwd"], cwd="exec-cwd")

    assert result.success
    assert result.stdout.strip() == "/root/exec-cwd"


async def test_exec_cwd_with_spaces(sandbox: K8sSandboxEnvironment) -> None:
    await sandbox.exec(["mkdir", "-p", "/root/dir with spaces"])

    result = await sandbox.exec(["pwd"], cwd="/root/dir with spaces")

    assert result.success
    assert result.stdout.strip() == "/root/dir with spaces"


async def test_exec_cwd_does_not_exist(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["pwd"], cwd="/exec-cwd/does/not/exist")

    assert result.returncode == 2
    assert "can't cd to /exec-cwd/does/not/exist" in result.stderr


async def test_exec_env(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["bash", "-c", "echo $FOO"], env={"FOO": "bar"})

    assert result.success
    assert result.stdout.strip() == "bar"


async def test_exec_env_not_persisted(sandbox: K8sSandboxEnvironment) -> None:
    await sandbox.exec(["bash", "-c", "echo $FOO"], env={"FOO": "bar"})
    result = await sandbox.exec(["bash", "-c", "echo $FOO"])

    assert result.success
    assert result.stdout.strip() == ""


async def test_exec_env_invalid_keys(sandbox: K8sSandboxEnvironment) -> None:
    result1 = await sandbox.exec(["bash", "-c", "echo $FOO"], env={"": "bar"})
    result2 = await sandbox.exec(["bash", "-c", "echo $FOO"], env={"F'O": "bar"})

    assert not result1.success
    assert not result2.success


async def test_exec_env_empty_values(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["bash", "-c", "echo $FOO"], env={"FOO": ""})

    assert result.success
    assert result.stdout.strip() == ""


async def test_exec_env_requiring_quotes(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["bash", "-c", "echo $FOO"], env={"FOO": "b'\"a r"})

    assert result.success
    assert result.stdout.strip() == "b'\"a r"


@pytest.mark.parametrize(
    "cmd",
    [["whoami"], ["bash", "-c", "echo $USER"], ["bash", "-c", "whoami"]],
)
async def test_exec_user(sandbox: K8sSandboxEnvironment, cmd: list[str]) -> None:
    # The nobody user is available in the default python:3.12-bookworm image.
    result = await sandbox.exec(cmd, user="nobody")

    assert result.success
    assert result.stdout.strip() == "nobody"


async def test_exec_user_when_specified_user_does_not_exist(
    sandbox: K8sSandboxEnvironment,
) -> None:
    with pytest.raises(K8sError) as excinfo:
        await sandbox.exec(["whoami"], user="foo")

    error_msg = str(excinfo.value.__cause__)
    assert (
        "The user parameter 'foo' provided to exec() does not appear to exist in the "
        "container" in error_msg
    )
    assert "https://k8s-sandbox.aisi.org.uk/design/limitations#exec-user" in error_msg


async def test_exec_user_when_not_running_as_root(
    sandbox_non_root: K8sSandboxEnvironment,
) -> None:
    with pytest.raises(K8sError) as excinfo:
        await sandbox_non_root.exec(["whoami"], user="nobody")

    error_msg = str(excinfo.value.__cause__)
    assert (
        "When a user parameter ('nobody') is provided to exec(), the container must be "
        "running as root" in error_msg
    )
    assert "https://k8s-sandbox.aisi.org.uk/design/limitations#exec-user" in error_msg


async def test_exec_user_when_runuser_not_installed(
    sandbox_busybox: K8sSandboxEnvironment,
) -> None:
    with pytest.raises(K8sError) as excinfo:
        await sandbox_busybox.exec(["whoami"], user="foo")

    error_msg = str(excinfo.value.__cause__)
    assert (
        "When a user parameter ('foo') is provided to exec(), the runuser binary "
        "must be installed in the container"
    ) in error_msg
    assert "https://k8s-sandbox.aisi.org.uk/design/limitations#exec-user" in error_msg


async def test_exec_does_not_raise_error_if_command_happens_to_use_runuser(
    sandbox: K8sSandboxEnvironment,
) -> None:
    # If an LLM happens to generate a command that uses `runuser`, we don't want to
    # raise an error.
    result = await sandbox.exec(["bash", "-c", "runuser -u foo -- whoami"])

    assert not result.success
    assert "runuser: user foo does not exist" in result.stderr


@pytest.mark.parametrize("cmd", [["bash", "-c", "sleep 10"], ["sleep", "10"]])
async def test_exec_timeout(
    sandbox: K8sSandboxEnvironment, cmd: list[str], log_err: LogCaptureFixture
) -> None:
    with pytest.raises(TimeoutError) as excinfo:
        await sandbox.exec(cmd, timeout=1)

    assert "Command timed out after 1s" in str(excinfo)
    assert not log_err.records


async def test_exec_raises_permission_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(PermissionError):
        # /etc/hosts is not an executable file.
        await sandbox.exec(["bash", "-c", "/etc/hosts"])

    assert not log_err.records


async def test_exec_does_not_raise_permission_error(
    sandbox: K8sSandboxEnvironment,
) -> None:
    result = await sandbox.exec(["mount", "-o", "remount,ro", "/"])

    # Despite "permission denied" being in stderr, the error was not 126
    # "Command invoked cannot execute".
    # https://tldp.org/LDP/abs/html/exitcodes.html#EXITCODESREF
    # So no exception is raised.
    assert "permission denied" in result.stderr.casefold()


async def test_exec_timeout_terminates_foreground_commands(
    sandbox: K8sSandboxEnvironment,
) -> None:
    sentinel = "/tmp/timeout-terminate.txt"
    with pytest.raises(TimeoutError):
        await sandbox.exec(["bash", "-c", f"sleep 2; touch {sentinel}"], timeout=1)

    # Wait for the sleep process to complete and check that no file was created.
    await sandbox.exec(["sleep", "2"])
    file_exists_result = await sandbox.exec(["test", "!", "-e", sentinel])

    assert file_exists_result.success


async def test_exec_unicode_decode_error(sandbox: K8sSandboxEnvironment) -> None:
    with pytest.raises(UnicodeDecodeError):
        await sandbox.exec(["head", "-c", "1024", "/bin/ls"])


async def test_exec_background_returns(sandbox: K8sSandboxEnvironment) -> None:
    # Check that a backgrounded process does not prevent the exec call from returning.
    result = await sandbox.exec(["bash", "-c", "sleep infinity &"])

    assert result.success


async def test_exec_background_completes(sandbox: K8sSandboxEnvironment) -> None:
    sentinel = "/tmp/bg-complete.txt"

    result = await sandbox.exec(["bash", "-c", f"(sleep 1 && touch {sentinel}) &"])

    assert result.success

    # Wait for the background process to complete and check that it was successful.
    await sandbox.exec(["sleep", "2"])
    file_exists_result = await sandbox.exec(["test", "-e", sentinel])
    assert file_exists_result.success


async def test_exec_background_completes_with_timeout(
    sandbox: K8sSandboxEnvironment,
) -> None:
    sentinel = "/tmp/bg-timeout-complete.txt"

    # The shell will be terminated after 1s, but we don't raise a TimeoutError because
    # the user-supplied command was backgrounded.
    await sandbox.exec(["bash", "-c", f"(sleep 2 && touch {sentinel}) &"], timeout=1)

    # Wait for the background process to complete and check that it was successful.
    await sandbox.exec(["sleep", "3"])
    file_exists_result = await sandbox.exec(["test", "-e", sentinel])
    assert file_exists_result.success


async def test_exec_file_not_found_error(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.exec(["cat", "/does/not/exist"])

    assert not result.success
    assert result.returncode != 0
    assert result.stdout == ""
    assert "no such file or directory" in result.stderr.casefold()


async def test_exec_stdout_truncation(sandbox: K8sSandboxEnvironment) -> None:
    limit_10_MiB = 10 * 1024**2  # 10 MiB
    with pytest.raises(OutputLimitExceededError) as excinfo:
        head_limit = limit_10_MiB + 1024  # 10 MiB + 1 KiB
        await sandbox.exec(["bash", "-c", f"yes | head -c {head_limit}"])

    truncated_output = excinfo.value.truncated_output
    assert truncated_output and len(truncated_output) == limit_10_MiB


async def test_exec_stderr_truncation(sandbox: K8sSandboxEnvironment) -> None:
    limit_10_MiB = 10 * 1024**2  # 10 MiB
    with pytest.raises(OutputLimitExceededError) as excinfo:
        head_limit = limit_10_MiB + 1024  # 10 MiB + 1 KiB
        await sandbox.exec(["bash", "-c", f"yes | head -n {head_limit} 1>&2"])

    truncated_output = excinfo.value.truncated_output
    assert truncated_output and len(truncated_output) == limit_10_MiB


async def test_exec_api_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with patch.object(
        WSClient, "read_channel", side_effect=ApiException(reason="my-reason")
    ):
        with pytest.raises(K8sError) as excinfo:
            await sandbox.exec(["true"])

    assert "my-reason" in str(excinfo.value.__cause__)
    # ApiException should be logged as an error.
    assert "my-reason" in log_err.records[0].message


async def test_exec_inspect_python_tool(sandbox: K8sSandboxEnvironment) -> None:
    python_code = dedent("""
    import os
    print(f"Home: {os.getenv('HOME')}")
    print("new\\nline")
    """)

    # This is how Inspect's standard python tool is implemented.
    result = await sandbox.exec(["python3"], input=python_code, timeout=5)

    assert result.success
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["Home: /root", "new", "line"]
    assert result.stderr == ""


async def test_exec_complex_bash_commend(sandbox: K8sSandboxEnvironment) -> None:
    # This is similar to how an AISI-internal stateful bash tool is implemented.
    cmd_template = dedent("""
    finally() {{
        pwd > /tmp/bash_tool_last_cwd
        export -p > /tmp/bash_tool_last_env
    }}
    trap 'finally' EXIT

    if [ -f /tmp/bash_tool_last_env ]; then
        source /tmp/bash_tool_last_env &> /dev/null
    fi

    if [ -f /tmp/bash_tool_last_cwd ]; then
        cd $(cat /tmp/bash_tool_last_cwd) &> /dev/null
    fi

    {command}
    """)

    await sandbox.exec(
        ["bash", "-c", cmd_template.format(command="cd /tmp && export FOO=bar")],
        timeout=5,
    )
    result = await sandbox.exec(
        ["bash", "-c", cmd_template.format(command="pwd && echo $FOO")], timeout=5
    )

    assert result.success
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["/tmp", "bar"]
    assert result.stderr == ""


async def test_exec_backgrounded_command_and_non_zero_exit_code(
    sandbox: K8sSandboxEnvironment,
) -> None:
    command = ["bash", "-c", "sleep infinity & exit 42"]

    result = await sandbox.exec(command, timeout=5)

    assert result.returncode == 42


async def test_exec_only_executes_once(sandbox: K8sSandboxEnvironment) -> None:
    # Historical issue: The remote command was being run twice.
    result = await sandbox.exec(["mkdir", "/tmp/only-once"])

    assert result.success


@pytest.mark.repeat(100)
async def test_exec_reliability(sandbox: K8sSandboxEnvironment) -> None:
    # Historical issue: sentinel value written to stdout occasionally appeared to be
    # buffered and only flushed once the process had completed (by `timeout`). Verify
    # that a simple command can be executed reliably without resulting in a timeout.
    result = await sandbox.exec(["pwd"], timeout=5)
    assert result.success


async def test_exec_timeout_which_ignores_sigterm(
    sandbox: K8sSandboxEnvironment,
) -> None:
    # Historical issue: certain commands ignore SIGTERM sent by timeout (e.g. mpg123
    # under certain conditions). Ensure that the command cannot run forever.
    result = await sandbox.exec(
        ["bash", "-c", "trap '' TERM; sleep infinity"], timeout=1
    )

    assert result.returncode == 137
    assert result.stderr == "Killed\n"


async def test_api_timeout_is_not_triggered_by_long_running_commands(
    sandbox: K8sSandboxEnvironment,
) -> None:
    with patch("k8s_sandbox._pod.op.API_TIMEOUT", 1):
        result = await sandbox.exec(["sleep", "3"])

    assert result.success
    assert result.returncode == 0


async def test_exec_with_default_user(
    sandbox_with_default_user: K8sSandboxEnvironment,
) -> None:
    result = await sandbox_with_default_user.exec(["whoami"])

    assert result.success
    assert result.stdout == "ubuntu\n"


async def test_exec_with_default_user_can_use_root(
    sandbox_with_default_user: K8sSandboxEnvironment,
) -> None:
    result = await sandbox_with_default_user.exec(["whoami"], user="root")

    assert result.success
    assert result.stdout == "root\n"


async def _wait_for_restart(sandbox: K8sSandboxEnvironment, timeout=30):
    """Block until any container has restartCount > 0."""
    pod_info = sandbox._pod.info
    client = k8s_client(pod_info.context_name)
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        pod = client.read_namespaced_pod(
            name=pod_info.name, namespace=pod_info.namespace
        )
        if any(
            cs.restart_count > 0
            for cs in (
                pod.status.container_statuses
                if pod.status and pod.status.container_statuses
                else []
            )
        ):
            return
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError("container did not restart in time")
        await asyncio.sleep(0.2)


async def test_exec_after_container_restart_warns(
    sandbox: K8sSandboxEnvironment,
    log_warning: LogCaptureFixture,
) -> None:
    await sandbox.exec(["kill", "1"])
    await _wait_for_restart(sandbox)

    await sandbox.exec(["ls"])

    assert "has restarted" in log_warning.records[0].message


async def test_exec_after_container_restart_raises(
    log_err: LogCaptureFixture,
) -> None:
    async with install_sandbox_environments(
        __file__, "values.yaml", restarted_container_behavior="raise"
    ) as envs:
        sandbox = envs["default"]
        await sandbox.exec(["kill", "1"])
        await _wait_for_restart(sandbox)

        with pytest.raises(K8sError) as exc_info:
            await sandbox.exec(["ls"])

        assert "has restarted" in str(exc_info.value.__cause__)
        assert "has restarted" in log_err.records[0].message


### #write_file() ###


async def test_write_file(sandbox: K8sSandboxEnvironment) -> None:
    dst = "test-write-file.txt"

    await sandbox.write_file(dst, "Hello, World!")

    cat_result = await sandbox.exec(["cat", dst])
    assert cat_result.stdout == "Hello, World!"
    # See round-trip test for binary data verification.


async def test_write_file_requiring_quotes(sandbox: K8sSandboxEnvironment) -> None:
    # The spaces in the filename require quotes. Also verify that quotes are escaped.
    dst = "test write file requiring '\"quotes\"'.txt"

    await sandbox.write_file(dst, "Hello, World!")

    cat_result = await sandbox.exec(["cat", dst])
    assert cat_result.stdout == "Hello, World!"


# The pod's cwd is /root.
@pytest.mark.parametrize("dst_dir", ["/", "/tmp-abs", "tmp-rel", "../tmp-rel"])
async def test_write_file_absolute_and_relative(
    sandbox: K8sSandboxEnvironment, dst_dir: str
) -> None:
    dst = os.path.join(dst_dir, "test_write_file_absolute_and_relative.txt")
    content = f"Hello, World! {dst_dir}"

    await sandbox.write_file(dst, content)

    cat_result = await sandbox.exec(["cat", dst])
    assert cat_result.stdout == content


@pytest.mark.parametrize("dst_dir", ["/absolute/new/dir", "relative/new/dir"])
async def test_write_file_creates_dirs(
    sandbox: K8sSandboxEnvironment, dst_dir: str
) -> None:
    dst = os.path.join(dst_dir, "test_write_file_creates_dirs.txt")

    await sandbox.write_file(dst, "Hello, World!")

    cat_result = await sandbox.exec(["cat", dst])
    assert cat_result.stdout == "Hello, World!"


async def test_write_file_generic_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(K8sError) as excinfo:
        await sandbox.write_file("/proc/version", "Hello, World!")

    assert "write error" in str(excinfo.value.__cause__)
    # PodError should be logged as an error.
    assert "write error" in log_err.records[0].message


async def test_write_file_api_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with patch.object(
        WSClient, "read_channel", side_effect=ApiException(reason="my-reason")
    ):
        with pytest.raises(K8sError) as excinfo:
            await sandbox.write_file("/api-error-file.txt", "Hello, World!")

    assert "my-reason" in str(excinfo.value.__cause__)
    # ApiException should be logged as an error.
    assert "my-reason" in log_err.records[0].message


async def test_write_file_overwrites_existing_regular_file(
    sandbox: K8sSandboxEnvironment,
) -> None:
    dst = "/test_write_file_overwrites_existing_regular_file.txt"
    await sandbox.exec(["sh", "-c", f"echo 'original contents' > {dst}"])

    await sandbox.write_file(dst, "new contents")

    cat_result = await sandbox.exec(["cat", dst])
    assert cat_result.stdout == "new contents"


async def test_write_file_overwrites_existing_special_file(
    sandbox: K8sSandboxEnvironment,
) -> None:
    # /etc/hosts behaves differently to regular files when written to.
    await sandbox.write_file("/etc/hosts", "Hello, World!")

    cat_result = await sandbox.exec(["cat", "/etc/hosts"])
    assert cat_result.stdout == "Hello, World!"


async def test_write_file_permission_error(
    sandbox_non_root: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(PermissionError):
        await sandbox_non_root.write_file("/root/file", "Hello, World!")

    assert not log_err.records


async def test_write_file_is_a_directory_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(IsADirectoryError):
        await sandbox.write_file("/root/", "Hello, World!")

    assert not log_err.records


### #read_file() ###


async def test_read_file(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.read_file("/proc/version")

    assert "Linux" in result
    # See round-trip test for binary data verification.


async def test_read_file_requiring_quotes(sandbox: K8sSandboxEnvironment) -> None:
    # The spaces in the filename require quotes. Also verify that quotes are escaped.
    file = "test read file requiring '\"quotes\"'.txt"
    await sandbox.write_file(file, "Hello, World!")

    result = await sandbox.read_file(file)

    assert result == "Hello, World!"


async def test_read_file_from_relative_path(sandbox: K8sSandboxEnvironment) -> None:
    await sandbox.write_file(
        "/root/relative/dir/test_read_file_from_relative_path.txt", "Hello, World!"
    )

    # The current working directory is /root.
    result = await sandbox.read_file(
        "relative/dir/test_read_file_from_relative_path.txt"
    )

    assert result == "Hello, World!"


async def test_read_file_maintains_line_endings(sandbox: K8sSandboxEnvironment) -> None:
    # The SandboxEnvironment interface documents that read_file() should maintain line
    # endings.
    expected = "Hello,\r\nWorld!\n"
    await sandbox.write_file("/line-endings.txt", expected)

    result = await sandbox.read_file("/line-endings.txt")

    assert result == expected


async def test_read_file_not_found(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(FileNotFoundError):
        await sandbox.read_file("/does/not/exist")

    assert not log_err.records


async def test_read_file_decode_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(UnicodeDecodeError) as excinfo:
        await sandbox.read_file("/bin/ls", text=True)

    assert "can't decode byte" in str(excinfo.value)
    assert not log_err.records


async def test_read_file_permission_error(
    sandbox_non_root: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(PermissionError):
        await sandbox_non_root.read_file("/etc/shadow")

    assert not log_err.records


async def test_read_file_is_a_directory_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with pytest.raises(IsADirectoryError):
        await sandbox.read_file("/etc")

    assert not log_err.records


async def test_read_file_limit_exceeded(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    await sandbox.write_file("large-file.txt", "a" * 2048)  # 2KiB

    # Patch limit down to 1KiB for the test to save us from writing a 100MiB file.
    with patch.object(SandboxEnvironmentLimits, "MAX_READ_FILE_SIZE", 1024):
        with pytest.raises(OutputLimitExceededError):
            await sandbox.read_file("large-file.txt", text=True)

    assert not log_err.records


async def test_read_file_api_error(
    sandbox: K8sSandboxEnvironment, log_err: LogCaptureFixture
) -> None:
    with patch.object(
        WSClient, "read_channel", side_effect=ApiException(reason="my-reason")
    ):
        with pytest.raises(K8sError) as excinfo:
            await sandbox.read_file("/etc/hosts")

    assert "my-reason" in str(excinfo.value.__cause__)
    # ApiException should be logged as an error.
    assert "my-reason" in log_err.records[0].message


### Round-trip ###


async def test_read_write_file_string_round_trip(
    sandbox: K8sSandboxEnvironment,
) -> None:
    pod_path = "/my/dir/round-trip.txt"
    contents = "Hello, World!\nRound-trip test.\n"

    await sandbox.write_file(pod_path, contents)
    result = await sandbox.read_file(pod_path, text=True)

    assert result == contents


async def test_read_write_file_bytes_round_trip(
    sandbox: K8sSandboxEnvironment, binary_data: bytes
) -> None:
    pod_path = "/my/dir/round-trip.bin"
    contents = b"Hello, World!\nRound-trip test.\n" + binary_data

    await sandbox.write_file(pod_path, contents)
    result = await sandbox.read_file(pod_path, text=False)

    assert result == contents


async def test_read_write_large_file_round_trip(
    sandbox: K8sSandboxEnvironment, binary_data: bytes
) -> None:
    pod_path = "/my/dir/round-trip-large.bin"
    contents = binary_data[:100] * 1024**2  # 100 MiB

    await sandbox.write_file(pod_path, contents)
    result = await sandbox.read_file(pod_path, text=False)

    assert result == contents


### Alternative images ###


async def test_sandbox_with_minimal_tools(
    sandbox_busybox: K8sSandboxEnvironment,
) -> None:
    # busybox has a minimal set of tools available. Verify that we're not relying on
    # any tools that are not available.
    exec_result = await sandbox_busybox.exec(["pwd"], timeout=5)
    await sandbox_busybox.write_file("/tmp/test-busybox", "Hello, World!")
    read_result = await sandbox_busybox.read_file("/tmp/test-busybox")

    assert exec_result.stdout.strip() == "/"
    assert read_result == "Hello, World!"


### SandboxConnection


async def test_can_get_sandbox_connection(sandbox: K8sSandboxEnvironment) -> None:
    result = await sandbox.connection()

    # kubectl exec -it agent-env-dwg883nv-default-0 -n default -c default -- bash -l
    assert re.match(
        r"^kubectl exec -it \S+ -n \S+ -c \S+ -- bash -l$", result.command
    ), result.command
    assert result.vscode_command is not None
    assert result.vscode_command[0] == "remote-containers.attachToK8sContainer"
    assert "name" in result.vscode_command[1]
    assert "namespace" in result.vscode_command[1]


async def test_can_get_sandbox_connection_with_specified_context() -> None:
    current_context_name = get_current_context_name()

    # Specify an explicit kubeconfig context name when installing the sandbox.
    async with install_sandbox_environments(
        __file__, "values.yaml", current_context_name
    ) as envs:
        sandbox = envs["default"]
        result = await sandbox.connection()

    # kubectl exec -it agent-env-dwg883nv-default-0 -n default -c default
    # --context minikube -- bash -l
    assert re.match(
        r"^kubectl exec -it \S+ -n \S+ -c \S+ --context \S+ -- bash -l$",
        result.command,
    ), result.command
    # The attachToK8sContainer command does not support passing in a context name, so
    # we don't return any VS Code command.
    assert result.vscode_command is None


async def test_can_get_sandbox_connection_with_specified_user(
    sandbox: K8sSandboxEnvironment,
) -> None:
    result = await sandbox.connection(user="agent")

    assert re.match(
        r"^kubectl exec -it \S+ -n \S+ -c default -- su -s /bin/bash -l agent$",
        result.command,
    ), result.command
    # The attachToK8sContainer command does not support passing in a user name, so
    # we don't return any VS Code command.
    assert result.vscode_command is None


async def test_can_get_sandbox_connection_with_default_user(
    sandbox_with_default_user: K8sSandboxEnvironment,
) -> None:
    result = await sandbox_with_default_user.connection()

    assert re.match(
        r"^kubectl exec -it \S+ -n \S+ -c default -- "
        r"su -s /bin/bash -l ubuntu$",
        result.command,
    ), result.command
    # The attachToK8sContainer command does not support passing in a user name, so
    # we don't return any VS Code command.
    assert result.vscode_command is None
