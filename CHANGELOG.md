# Changelog

## Unreleased

- Add `unset` magic string for `runtimeClassName` which will remove the field from the pod spec.
- Add ignored `timeout_retry` parameter to `exec()` method.
- Always capture the output of `helm uninstall` so that errors can contain meaningful information.
- Add support for `inspect sandbox cleanup k8s` command to uninstall all Inspect Helm charts.
- Remove use of Inspect's deleted `SANDBOX` log level in favour of `trace_action()` and `trace_message()` functions.
- Initial release.
