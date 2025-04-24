import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import yaml

from k8s_sandbox._helm import ValuesSource
from k8s_sandbox.compose._converter import convert_compose_to_helm_values


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
    """Infers whether a file is a Docker Compose file based on the filename.

    This errs on the side of false negatives to avoid automatic conversion of files
    which may not be Docker Compose files.

    Returns:
        True if the file name _ends_ in `compose.yaml` or `compose.yml`, False
        otherwise.
    """
    return file.name.endswith("compose.yaml") or file.name.endswith("compose.yml")
