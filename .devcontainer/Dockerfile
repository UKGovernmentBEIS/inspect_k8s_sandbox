# See here for image contents: https://github.com/microsoft/vscode-dev-containers/tree/v0.148.1/containers/python-3/.devcontainer/base.Dockerfile
ARG VARIANT="3.11"
FROM mcr.microsoft.com/vscode/devcontainers/python:0-${VARIANT}

# https://github.com/UKGovernmentBEIS/inspect_ai/issues/51
ENV XDG_RUNTIME_DIR=/tmp

# Install Cilium CLI. Previously, the ghcr.io/audacioustux/devcontainers/cilium:1
# devcontainer feature was used, but it doesn't allow a specific version of the CLI to
# be installed and the latest version failed at `cilium install`.
ARG CILIUM_CLI_VERSION=v0.16.15
ARG CILIUM_CLI_ARCH=amd64
RUN curl -L --fail --remote-name-all https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CILIUM_CLI_ARCH}.tar.gz{,.sha256sum} && \
    sha256sum --check cilium-linux-${CILIUM_CLI_ARCH}.tar.gz.sha256sum && \
    tar xzvfC cilium-linux-${CILIUM_CLI_ARCH}.tar.gz /usr/local/bin && \
    rm cilium-linux-${CILIUM_CLI_ARCH}.tar.gz{,.sha256sum}

USER vscode
RUN pip3 --disable-pip-version-check --no-cache-dir install -U \
  'uv==0.8.22'
