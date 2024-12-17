from typing import AsyncGenerator

import pytest
import pytest_asyncio

from aisitools.k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.aisitools.k8s_sandbox.utils import install_sandbox_environments

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(__file__, "dns-values.yaml") as envs:
        yield envs["default"]


# The inclusion of additionalDnsRecords or ports should result in a DNS entry being
# created for the service using its service name.
@pytest.mark.parametrize("hostname", ["victim", "victim-google", "victim-ports"])
async def test_victim_is_resolved_by_service_name(
    sandbox: K8sSandboxEnvironment, hostname: str
):
    result = await sandbox.exec(["curl", "-sI", hostname], timeout=10)

    assert "Server: nginx/1.27.0" in result.stdout


async def test_victim_is_resolved_by_additional_dns_record(
    sandbox: K8sSandboxEnvironment,
):
    # google.com is an additional DNS record in the Helm chart values which resolves to
    # victim-google.
    result = await sandbox.exec(["curl", "-sI", "http://google.com"], timeout=10)

    assert "Server: nginx/1.27.0" in result.stdout


async def test_victim_is_resolved_by_env_var_and_service_name(
    sandbox: K8sSandboxEnvironment,
):
    # Verify that $AGENT_ENV-victim is resolved for backward compatibility.
    result = await sandbox.exec(
        ["bash", "-c", "curl -sI http://${AGENT_ENV}-victim"], timeout=10
    )

    assert "Server: nginx/1.27.0" in result.stdout


async def test_netcat_on_victim(sandbox: K8sSandboxEnvironment):
    # If victim's ClusterIP Service is not headless, netcat will hang on ports which
    # aren't explicitly listed on the Service.
    result = await sandbox.exec(["nc", "-zv", "victim", "1-80"], timeout=10)

    assert "port 79 (tcp) failed: Connection refused" in result.stderr
    assert "80 port [tcp/http] succeeded" in result.stderr


async def test_ping_on_victim(sandbox: K8sSandboxEnvironment):
    # If victim's ClusterIP Service is not headless, ping will hang.
    result = await sandbox.exec(["ping", "-c", "1", "victim"], timeout=10)

    assert "1 packets transmitted, 1 received" in result.stdout


async def test_non_existent_service(sandbox: K8sSandboxEnvironment):
    result = await sandbox.exec(
        ["curl", "-sI", "http://non-existent-service"],
        timeout=10,
    )

    assert not result.success
