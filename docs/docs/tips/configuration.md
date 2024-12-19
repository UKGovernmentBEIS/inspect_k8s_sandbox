# Advanced Configuration

## Helm install timeout { #helm-install-timeout }

The built-in Helm install timeout is 5 minutes. If you're running large eval sets and
expect to run into cluster capacity issues, you can increase the timeout by setting the
`INSPECT_HELM_TIMEOUT` environment variable to a number of seconds.

```sh
export INSPECT_HELM_TIMEOUT=21600   # 6 hours
```

## Structured logging truncation threshold

By default, each key/value pair (e.g. an exec command's output) logged to Python's
`logging` module (via structured JSON logging) is truncated to 1000 characters. This is
to prevent logs from becoming excessively large when e.g. a model runs a command which
produces a large amount of output. This can be adjusted by setting the
`INSPECT_LOG_TRUNCATION_THRESHOLD` environment variable to a number of characters.

```sh
export INSPECT_LOG_TRUNCATION_THRESHOLD=5000
```
