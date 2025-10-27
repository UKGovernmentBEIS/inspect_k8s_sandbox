import asyncio
import os
from pathlib import Path

from inspect_ai import Task, eval, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import (
    ChatMessageAssistant,
    ModelOutput,
)
from inspect_ai.scorer import includes
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import SandboxEnvironmentSpec, sandbox

from k8s_sandbox._sandbox_environment import (
    K8sSandboxEnvironmentConfig,
)

# Runs a Task with many epochs (repeats) each of which spins making sleep 1 calls
# until the task times out (120 seconds).

success_str = "sandbox_exec_success"


@task
def files_open_task():
    return Task(
        dataset=MemoryDataset([Sample(input="Input", target=success_str)]),
        sandbox=SandboxEnvironmentSpec(
            "k8s",
            K8sSandboxEnvironmentConfig(
                chart=str(Path(__file__).parent / "basic-chart"),
                values=None,
            ),
        ),
        solver=[sleepy_solver()],
        scorer=includes(),
        max_messages=1,  # Only one solver call, it handles timeout internally
    )


@solver
def sleepy_solver():
    async def solve(state: TaskState, generate: Generate):
        result = await sleep_loop(timeout=120)
        state.messages.append(ChatMessageAssistant(content=result, source="generate"))
        state.output = ModelOutput.from_content(model="mock", content=result)
        return state

    return solve


async def sleep_loop(timeout: int = 120) -> str:
    start_time = asyncio.get_event_loop().time()
    end_time = start_time + timeout
    count = 0

    while asyncio.get_event_loop().time() < end_time:
        result = await sandbox().exec(["sleep", "1"], timeout=5)
        if result.returncode != 0:
            return f"error\nsleep call {count} failed\n{result}"
        count += 1
    return f"{success_str}\ncompleted {count} sleep calls"


def run_diagnostic_eval(
    epochs: int = 500,
    max_helm_install: int = 300,
    max_helm_uninstall: int = 500,
    max_pod_ops: int = 500,
) -> float:
    os.environ["INSPECT_MAX_HELM_INSTALL"] = str(max_helm_install)
    os.environ["INSPECT_MAX_HELM_UNINSTALL"] = str(max_helm_uninstall)
    os.environ["INSPECT_MAX_POD_OPS"] = str(max_pod_ops)
    logs = eval(
        tasks=[files_open_task()],
        model="mockllm/model",
        max_samples=epochs,  # Let all epochs run concurrently.
        epochs=epochs,
    )
    assert logs[0].results is not None
    return logs[0].results.scores[0].metrics["accuracy"].value


if __name__ == "__main__":
    run_diagnostic_eval()
