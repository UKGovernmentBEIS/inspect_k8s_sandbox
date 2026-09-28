import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CHART = _REPO_ROOT / "src/k8s_sandbox/resources/helm/agent-env/Chart.yaml"


def test_chart_version_matches_package_version() -> None:
    # The chart is bundled in the package rather than published separately, so its
    # version must track pyproject.toml's.
    match = re.search(r'^version = "([^"]+)"', _PYPROJECT.read_text(), re.MULTILINE)
    assert match is not None, "no [project] version in pyproject.toml"
    package_version = match.group(1)

    chart_version = str(yaml.safe_load(_CHART.read_text())["version"])

    assert chart_version == package_version
