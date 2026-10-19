import ipaddress
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
import yaml

from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import (
    assert_proper_ports_are_open,
    install_sandbox_environments,
)

# Mark all tests in this module as requiring a Kubernetes cluster.
pytestmark = pytest.mark.req_k8s


@pytest_asyncio.fixture(scope="module")
async def sandbox_envs_ports_no_net() -> AsyncGenerator[
    dict[str, K8sSandboxEnvironment], None
]:
    async with install_sandbox_environments(
        __file__, "ports-no-net-values.yaml"
    ) as envs:
        yield envs


@pytest.fixture
def sandbox_ports_no_net(
    sandbox_envs_ports_no_net: dict[str, K8sSandboxEnvironment],
) -> K8sSandboxEnvironment:
    return sandbox_envs_ports_no_net["default"]


@pytest.mark.parametrize(
    "host_to_mapped_ports",
    [
        {"host": "ports-specified", "open_ports": ["8080"], "closed_ports": ["9090"]},
        {
            "host": "ports-not-specified",
            "open_ports": ["8080", "9090"],
            "closed_ports": [],
        },
    ],
)
async def test_only_specified_ports_are_open_no_networks(
    sandbox_ports_no_net: K8sSandboxEnvironment, host_to_mapped_ports
):
    host = host_to_mapped_ports["host"]
    reachable = len(host_to_mapped_ports["open_ports"]) > 0

    result = await sandbox_ports_no_net.exec(
        ["ping", "-c", "1", "-W", "5", host], timeout=10
    )

    assert not reachable or result.returncode == 0, (
        f"Host {host} should be reachable but is not: {result}"
    )
    await assert_proper_ports_are_open(sandbox_ports_no_net, host_to_mapped_ports)


async def test_cross_sandbox_ingress_requires_explicit_allow(
    sandbox_envs_ports_no_net: dict[str, K8sSandboxEnvironment], tmp_path: Path
) -> None:
    target = sandbox_envs_ports_no_net["ports-not-specified"]
    result = await target.exec(
        [
            "python3",
            "-c",
            "import socket; print(socket.gethostbyname(socket.gethostname()))",
        ],
        timeout=10,
    )
    assert result.returncode == 0, result
    target_ip = str(ipaddress.ip_address(result.stdout.strip()))
    target_release = target.release.release_name
    await assert_proper_ports_are_open(
        sandbox_envs_ports_no_net["default"],
        {"host": target_ip, "open_ports": ["8080", "9090"], "closed_ports": []},
    )
    values = {
        "services": {"default": {"image": "alpine:3.20"}},
        "labels": {"inspect/test-ingress-source": target_release},
        "additionalResources": [
            {
                "apiVersion": "cilium.io/v2",
                "kind": "CiliumNetworkPolicy",
                "metadata": {"name": "{{ .Release.Name }}-test-target-egress"},
                "spec": {
                    "endpointSelector": {
                        "matchLabels": {
                            "app.kubernetes.io/instance": "{{ .Release.Name }}"
                        }
                    },
                    "egress": [
                        {
                            "toEndpoints": [
                                {
                                    "matchLabels": {
                                        "app.kubernetes.io/instance": target_release,
                                        "inspect/service": "ports-not-specified",
                                    }
                                }
                            ]
                        }
                    ],
                },
            }
        ],
    }
    values_path = tmp_path / "cross-sandbox-values.yaml"
    values_path.write_text(yaml.safe_dump(values))
    async with install_sandbox_environments(__file__, str(values_path)) as source_envs:
        # Both ports are listening and source egress allows this target. The extra
        # ingress policy permits only 9090; default-deny must still block 8080.
        await assert_proper_ports_are_open(
            source_envs["default"],
            {"host": target_ip, "open_ports": ["9090"], "closed_ports": ["8080"]},
        )
