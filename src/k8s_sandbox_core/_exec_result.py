"""Lightweight ExecResult dataclass matching inspect_ai's interface."""

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T", str, bytes)


@dataclass
class ExecResult(Generic[T]):
    """The result of executing a command."""

    success: bool
    returncode: int
    stdout: T
    stderr: T
