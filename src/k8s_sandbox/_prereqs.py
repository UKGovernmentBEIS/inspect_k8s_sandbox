import logging

from inspect_ai._util.error import PrerequisiteError  # TODO: Using private package.
from inspect_ai.util import subprocess
from semver import Version

logger = logging.getLogger(__name__)

# This was picked as it is over 2 years old. It may be possible that older versions
# work but would require testing.
MINIMUM_HELM_VERSION = "3.10.0"


async def validate_prereqs() -> None:
    await _validate_helm()


async def _validate_helm() -> None:
    """Validate that helm is installed and the version is >= REQUIRED_HELM_VERSION."""
    try:
        result = await subprocess(["helm", "version", "--short"])
    # Inspect's `subprocess` raises FileNotFoundError if the command is not found.
    except FileNotFoundError:
        _raise("Helm is not installed.")
    except Exception:
        logger.warning(
            "Unexpected exception when executing `helm version`.", exc_info=True
        )
        _raise("Failed to determine which version of helm is installed.")
    installed_version = _parse_version(result.stdout)
    if installed_version.compare(MINIMUM_HELM_VERSION) < 0:
        _raise(f"Found version {installed_version}.")


def _raise(message: str) -> None:
    raise PrerequisiteError(
        "K8s sandbox environments require helm (CLI) version >= "
        f"{MINIMUM_HELM_VERSION}. {message} "
        "See https://helm.sh/docs/intro/install/"
    )


def _parse_version(version: str) -> Version:
    # Typical output: "v3.15.3+g3bb50bb"
    if version.startswith("v"):
        return Version.parse(version[1:])
    return Version.parse(version)
