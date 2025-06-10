# Automatic Docker Compose to Helm Values Translation

The `k8s_sandbox` package supports automatically converting Docker Compose files to Helm
values files which are compatible with the [built-in Helm chart](built-in-chart.md) at
run time. This is done transparently: files won't be added to your repository.

```py
return Task(
    ...,
    sandbox=("k8s", "compose.yaml"),
)
```

The following file names are supported for automatic translation: `compose.yaml`,
`compose.yml`, `docker-compose.yaml`, `docker-compose.yml`. You must explicitly specify
the relevant compose file name in the `sandbox` parameter; only `helm-values.yaml` and
`values.yaml` are automatically discovered. This is to prevent unintentional translation
of Docker Compose files (e.g. if your Helm values file were misnamed).

Docker Compose files are first validated against the [Compose
Spec](https://github.com/compose-spec/compose-spec).

## Rationale

This functionality intends to facilitate running some of the community-maintained evals
which have not been (and may never be) ported to Helm `values.yaml`. Whilst it is easy
to convert a `compose.yaml` file to a `values.yaml` file, it does add a maintenance
burden, especially if an individual making changes in future does not have access to a
Kubernetes cluster to test the changes.

## Limitations

Only basic Docker compose functionality is supported. For more complex needs, please
write a Helm values file directly.

Images will have to be available to the Kubernetes cluster; they won't be built or
pushed for you.

For internal, non-community eval suites, native Helm `values.yaml` files are still
preferred over the automatic translation of `compose.yaml` files for a number of
reasons:

 * To support the whole set of Helm chart and Kubernetes features
 * To explicitly _not_ support Docker for certain evals (reducing maintenance burden and
   discourage use of Docker which lacks security features of Kubernetes)
 * To be more expressive about which services should get a DNS entry
 * To support more powerful readiness and liveness probes

## Internet Access

As per the built-in Helm chart, internet access is disabled by default. This is in
contrast to Docker Compose. There is no native way of specifying which domains should be
accessible in Docker Compose. To express which domains should be accessible when running
an eval in k8s, use the `x-inspect_k8s_sandbox` extension in the Docker Compose file.

```yaml
services:
  myservice:
    image: ubuntu
x-inspect_k8s_sandbox:
  allow_domains:
    - google.com
```
