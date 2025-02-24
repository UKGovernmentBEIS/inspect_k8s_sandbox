import logging
import subprocess
from pathlib import Path

import yaml

from k8s_sandbox._compose_adapter import convert

# A prototype script to convert from a Docker compose.yaml file into a helm-values.yaml
# file suitable for the built-in Helm chart.
# Feels like this is missing a design pattern.

# Documentation to include elsewhere:
# - This is by no means a complete conversion script.
# - It only supports basic Docker Compose features.

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def main() -> None:
    samples = Path(__file__).parent / "samples"
    verify_sample(samples / "compose.yaml")
    # verify_cybench_samples()


def verify_sample(compose_path: Path) -> None:
    samples = Path(__file__).parent / "samples"
    target = samples / "helm-values.yaml"
    try:
        helm = convert(compose_path)
    except Exception as e:
        raise ValueError(f"Error converting {compose_path}.") from e
    yaml_str = yaml.dump(helm, sort_keys=False)
    target.write_text(yaml_str)
    verify_helm_template(target)


def verify_cybench_samples() -> None:
    cybench = Path(__file__).parent / "samples" / "cybench"
    for cybench_sample in cybench.iterdir():
        verify_sample(cybench_sample / "compose.yaml")


def verify_helm_template(values_path: Path) -> None:
    helm_chart_dir = (
        Path(__file__).parent.parent.parent
        / "src"
        / "k8s_sandbox"
        / "resources"
        / "helm"
        / "agent-env"
    )
    subprocess.run(
        [
            "helm",
            "template",
            "my-validation-release",
            helm_chart_dir,
            "-f",
            values_path,
        ],
        text=True,
        check=True,
    )


if __name__ == "__main__":
    main()
