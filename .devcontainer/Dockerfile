FROM mcr.microsoft.com/devcontainers/python:1-3.11

# The base image ships a Yarn apt source whose GPG key has expired,
# which breaks apt-get update (used by devcontainer features).
RUN rm -f /etc/apt/sources.list.d/yarn.list

# https://github.com/UKGovernmentBEIS/inspect_ai/issues/51
ENV XDG_RUNTIME_DIR=/tmp
USER vscode
RUN pip3 --disable-pip-version-check --no-cache-dir install -U \
  'uv==0.8.22'
