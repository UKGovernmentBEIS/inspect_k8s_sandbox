from setuptools import find_packages, setup

desc = (
    "Placeholder package only. Please install from source "
    "https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox. A Kubernetes Sandbox "
    "Environment for Inspect."
)
setup(
    name="inspect-k8s-sandbox",
    version="0.0.2",
    description=desc,
    long_description=desc,
    author="UK AI Safety Institute",
    packages=find_packages(),
    url="https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox",
)
