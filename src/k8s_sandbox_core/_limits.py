"""Output size limits and errors for sandbox operations.

Constants match inspect_ai's SandboxEnvironmentLimits values.
"""


class SandboxLimits:
    """Output size limits for sandbox operations."""

    MAX_EXEC_OUTPUT_SIZE = 10 * 1024**2  # 10 MiB
    MAX_EXEC_OUTPUT_SIZE_STR = "10 MiB"
    MAX_READ_FILE_SIZE = 100 * 1024**2  # 100 MiB
    MAX_READ_FILE_SIZE_STR = "100 MiB"


class OutputLimitExceededError(Exception):
    """Raised when sandbox output exceeds the configured size limit."""

    def __init__(self, limit_str: str, truncated_output: str | None) -> None:
        self.limit_str = limit_str
        self.truncated_output = truncated_output
        super().__init__(
            f"The sandbox output stream limit of {self.limit_str} was exceeded."
        )
