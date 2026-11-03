FROM python:3.12-bookworm

ARG HELM_VERSION=3.17.0
RUN curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" | tar xz \
    && mv linux-amd64/helm /usr/local/bin/helm && rm -rf linux-amd64

COPY . /app
WORKDIR /app
RUN pip install --no-cache-dir .

CMD ["python", "/app/test/diagnostics/two-cluster-config/test_eval.py"]
