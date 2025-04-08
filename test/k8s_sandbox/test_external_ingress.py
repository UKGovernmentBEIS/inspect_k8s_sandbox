import contextlib
import time
from typing import AsyncGenerator, Generator

import pytest_asyncio
from kubernetes import client  # type: ignore

from k8s_sandbox._kubernetes_api import get_default_namespace
from k8s_sandbox._sandbox_environment import K8sSandboxEnvironment
from test.k8s_sandbox.utils import install_sandbox_environments


@pytest_asyncio.fixture(scope="module")
async def sandbox() -> AsyncGenerator[K8sSandboxEnvironment, None]:
    async with install_sandbox_environments(
        __file__, "external-ingress-values.yaml"
    ) as envs:
        yield envs["default"]


async def test_can_access_nginx_from_outside_helm_release(
    sandbox: K8sSandboxEnvironment,
) -> None:
    # Get the ClusterIP of the default service
    ip_address = (await sandbox.exec(["hostname", "-i"], timeout=10)).stdout.strip()

    # Create a job which tries to access the default service of the agent-env.
    job_name = "job-test-external-ingress"
    namespace = get_default_namespace(None)
    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "test",
                            "image": "nicolaka/netshoot:v0.13",
                            "command": [
                                "curl",
                                f"http://{ip_address}:80",
                                "-s",
                            ],
                        }
                    ],
                    "restartPolicy": "Never",
                }
            },
        },
    }

    @contextlib.contextmanager
    def job() -> Generator[None, None, None]:
        try:
            batch_v1 = client.BatchV1Api()
            batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)
            yield
        finally:
            batch_v1.delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Background"),
            )

    def get_job_exit_code() -> int:
        core_v1 = client.CoreV1Api()
        timeout = 60
        for _ in range(timeout):
            time.sleep(1)
            pods = core_v1.list_namespaced_pod(
                namespace=namespace, label_selector=f"job-name={job_name}"
            )
            if not pods.items:
                continue
            pod = pods.items[0]
            if (
                pod.status.container_statuses
                and pod.status.container_statuses[0].state.terminated
            ):
                return pod.status.container_statuses[0].state.terminated.exit_code
        raise TimeoutError(f"Job did not complete within {timeout} seconds.")

    with job():
        exit_code = get_job_exit_code()

    # The job will only have an exit code of 0 if the curl command was successful.
    assert exit_code == 0, exit_code
