# Changelog

## Unreleased

- Increase default Helm install timeout from 5 to 10 minutes.
- Prevent DNS exfiltration attacks by limiting which domains can be looked up (when using the built-in Helm chart).
- If a namespace is not includes in the kubeconfig context, default to a namespace named "default".
- Add `CLUSTER_DEFAULT` magic string for `runtimeClassName` which will remove the field from the pod spec.
- Add ignored `timeout_retry` parameter to `exec()` method.
- Always capture the output of `helm uninstall` so that errors can contain meaningful information.
- Add support for `inspect sandbox cleanup k8s` command to uninstall all Inspect Helm charts.
- Remove use of Inspect's deleted `SANDBOX` log level in favour of `trace_action()` and `trace_message()` functions.
- Initial release.
