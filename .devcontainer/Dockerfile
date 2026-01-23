# See here for image contents: https://github.com/devcontainers/images/tree/main/src/python
ARG VARIANT="3.11"
FROM mcr.microsoft.com/devcontainers/python:1-${VARIANT}

# https://github.com/UKGovernmentBEIS/inspect_ai/issues/51
ENV XDG_RUNTIME_DIR=/tmp
USER vscode
RUN pip3 --disable-pip-version-check --no-cache-dir install -U \
  'uv==0.8.22'
