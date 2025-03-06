import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import yaml

from k8s_sandbox._compose.converter import convert_compose_to_helm_values
from k8s_sandbox._helm import ValuesSource


class ComposeValuesSource(ValuesSource):
    """A ValuesSource which converts a Docker Compose file to Helm values on demand."""

    def __init__(self, compose_file: Path) -> None:
        self._compose_file = compose_file

    @contextmanager
    def values_file(self) -> Generator[Path | None, None, None]:
        converted = convert_compose_to_helm_values(self._compose_file)
        with tempfile.NamedTemporaryFile("w") as f:
            f.write(yaml.dump(converted, sort_keys=False))
            f.flush()
            yield Path(f.name)


def is_docker_compose_file(file: Path) -> bool:
    """Infers whether a file is a Docker Compose file."""
    # Also true for `docker-compose.yaml`.
    return file.name.endswith("compose.yaml") or file.name.endswith("compose.yml")
