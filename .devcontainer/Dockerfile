# See here for image contents: https://github.com/microsoft/vscode-dev-containers/tree/v0.148.1/containers/python-3/.devcontainer/base.Dockerfile
ARG VARIANT="3.11"
FROM mcr.microsoft.com/vscode/devcontainers/python:0-${VARIANT}

# https://github.com/UKGovernmentBEIS/inspect_ai/issues/51
ENV XDG_RUNTIME_DIR=/tmp
USER vscode
RUN pip3 --disable-pip-version-check --no-cache-dir install -U \
  'uv==0.8.22'
