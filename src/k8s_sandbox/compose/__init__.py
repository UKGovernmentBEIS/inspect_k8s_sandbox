"""Limited Docker Compose support for the k8s_sandbox package."""

from k8s_sandbox.compose._compose import is_docker_compose_file
from k8s_sandbox.compose._converter import convert_compose_to_helm_values

__all__ = [
    "is_docker_compose_file",
    "convert_compose_to_helm_values",
]
