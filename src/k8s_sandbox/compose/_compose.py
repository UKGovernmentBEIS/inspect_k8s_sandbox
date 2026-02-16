from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import yaml
from inspect_ai.util._sandbox.compose import ComposeConfig

from k8s_sandbox._helm import ValuesSource, validate_no_null_values
from k8s_sandbox.compose._converter import convert_compose_to_helm_values


class ComposeValuesSource(ValuesSource):
    """A ValuesSource which converts a Docker Compose file to Helm values on demand."""

    def __init__(self, compose_file: Path) -> None:
        self._compose_file = compose_file

    @contextmanager
    def values_file(self) -> Generator[Path | None, None, None]:
        converted = convert_compose_to_helm_values(self._compose_file)
        # Validate the converted values before writing to temp file
        validate_no_null_values(converted, f"compose file {self._compose_file}")
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(yaml.dump(converted, sort_keys=False))
            f.flush()
            yield Path(f.name)


class ComposeConfigValuesSource(ValuesSource):
    """A ValuesSource which converts an in-memory ComposeConfig to Helm values."""

    def __init__(self, compose_config: ComposeConfig) -> None:
        self._compose_config = compose_config

    @contextmanager
    def values_file(self) -> Generator[Path | None, None, None]:
        # Serialize ComposeConfig to a dict matching what yaml.safe_load produces
        # from a compose.yaml file, then write to a temp file for the converter.
        compose_dict = self._compose_config.model_dump(exclude_none=True, by_alias=True)
        with tempfile.NamedTemporaryFile("w", suffix="-compose.yaml") as compose_f:
            compose_f.write(yaml.dump(compose_dict, sort_keys=False))
            compose_f.flush()
            converted = convert_compose_to_helm_values(Path(compose_f.name))
        validate_no_null_values(converted, "ComposeConfig")
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(yaml.dump(converted, sort_keys=False))
            f.flush()
            yield Path(f.name)


def is_docker_compose_file(file: Path) -> bool:
    """Infers whether a file is a Docker Compose file based on the filename.

    This errs on the side of false negatives to avoid automatic conversion of files
    which may not be Docker Compose files.

    Returns:
        True if the file name _ends_ in `compose.yaml` or `compose.yml`, False
        otherwise.
    """
    return file.name.endswith("compose.yaml") or file.name.endswith("compose.yml")
