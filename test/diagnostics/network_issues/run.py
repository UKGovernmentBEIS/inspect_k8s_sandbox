import asyncio
import os
import random

from inspect_ai import Task, eval, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import (
    ChatMessageAssistant,
    ModelOutput,
)
from inspect_ai.scorer import includes
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import sandbox

# Runs a Task with many epochs (repeats) each of which simply curls a domain to quantify
# DNS or network access issues. The scoring is 1.0 for success and 0.0 for a timeout or
# other error.

success_str = "sandbox_exec_success"
domains = ["google.com", "yahoo.com", "bing.com", "wikipedia.org", "amazon.com"]


@task
def internet_access_task(post_curl_sleep: int):
    return Task(
        dataset=MemoryDataset([Sample(input="Input", target=success_str)]),
        sandbox=("k8s", "helm-values.yaml"),
        solver=[internet_access_solver(post_curl_sleep)],
        scorer=includes(),
    )


@solver
def internet_access_solver(post_curl_sleep: int):
    async def solve(state: TaskState, generate: Generate):
        result = await curl_domain()
        state.messages.append(ChatMessageAssistant(content=result, source="generate"))
        state.output = ModelOutput.from_content(model="mock", content=result)
        # Keep the eval going a while longer so that the Pod sticks around in case the
        # issue is exacerbated by number of Pods or number of Cilium Network Policies.
        await asyncio.sleep(post_curl_sleep)
        return state

    return solve


async def curl_domain() -> str:
    target_domain = random.choice(domains)
    try:
        result = await sandbox().exec(["curl", "-I", target_domain], timeout=20)
    except TimeoutError:
        return f"timeout\n{target_domain}"
    if result.returncode != 0:
        return f"error\n{target_domain}\n{result}"
    return f"{success_str}\n{target_domain}\n{result}"


def run_diagnostic_eval(
    epochs: int = 500,
    post_curl_sleep: int = 300,
    max_helm_install: int = 100,
    max_helm_uninstall: int = 100,
    max_pod_ops: int = 200,
) -> float:
    os.environ["INSPECT_MAX_HELM_INSTALL"] = str(max_helm_install)
    os.environ["INSPECT_MAX_HELM_UNINSTALL"] = str(max_helm_uninstall)
    os.environ["INSPECT_MAX_POD_OPS"] = str(max_pod_ops)
    logs = eval(
        tasks=[internet_access_task(post_curl_sleep)],
        model="mockllm/model",
        max_samples=epochs,  # Let all epochs run concurrently.
        epochs=epochs,
    )
    assert logs[0].results is not None
    return logs[0].results.scores[0].metrics["accuracy"].value


if __name__ == "__main__":
    run_diagnostic_eval()
