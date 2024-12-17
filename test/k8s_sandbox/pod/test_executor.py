import asyncio
import threading
from time import sleep
from typing import Generator
from unittest.mock import patch

import pytest
from pytest import MonkeyPatch

from aisitools.k8s_sandbox._pod.executor import PodOpExecutor


@pytest.fixture(autouse=True)
def reset_singleton() -> Generator:
    # Ensure that each test starts with a fresh singleton instance.
    PodOpExecutor._instance = None
    yield


def test_get_instance() -> None:
    result1 = PodOpExecutor.get_instance()
    result2 = PodOpExecutor.get_instance()

    assert result1 == result2


def test_default_max_workers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("INSPECT_MAX_POD_OPS", raising=False)

    with patch("os.cpu_count", return_value=4):
        actual = PodOpExecutor.get_instance()

    assert actual._max_workers == 16


def test_max_workers_via_env_var(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("INSPECT_MAX_POD_OPS", "42")

    actual = PodOpExecutor.get_instance()

    assert actual._max_workers == 42


async def test_queue_operation(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("INSPECT_MAX_POD_OPS", "10")
    executor = PodOpExecutor.get_instance()

    op1 = executor.queue_operation(lambda: _synchronous_operation(1))
    op2 = executor.queue_operation(lambda: _synchronous_operation(2))

    result1, result2 = await asyncio.gather(op1, op2)
    assert result1 == (1, "pod-op-executor_0")
    assert result2 == (2, "pod-op-executor_1")


async def test_queue_more_operations_than_max_workers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("INSPECT_MAX_POD_OPS", "2")
    executor = PodOpExecutor.get_instance()

    op1 = executor.queue_operation(lambda: _synchronous_operation(1))
    op2 = executor.queue_operation(lambda: _synchronous_operation(2))
    op3 = executor.queue_operation(lambda: _synchronous_operation(3))

    result1, result2, result3 = await asyncio.gather(op1, op2, op3)
    assert result1 == (1, "pod-op-executor_0")
    assert result2 == (2, "pod-op-executor_1")
    # The third operation should be executed by one of the two existing workers.
    assert result3 == (3, "pod-op-executor_0") or result3 == (3, "pod-op-executor_1")


def _synchronous_operation(value: int) -> tuple[int, str]:
    sleep(1)
    return value, threading.current_thread().name
