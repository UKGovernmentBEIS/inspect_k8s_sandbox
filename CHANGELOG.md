# Changelog

## Unreleased

- Ignore `x-local` key in Docker Compose files (Inspect-specific extension).
- Enhanced `additionalResources` to support full Helm templating.
- Support `user` parameter on `K8sSandboxEnvironment.exec()` (only when container is running as root and `runuser` is installed).
- Support `user` parameter on `K8sSandboxEnvironment.connection()` (returns `SandboxConnection`).
- Add `SandboxConnection` support for human agent baselining and connecting to a sandbox for debugging.
- Add support for specifying a kubeconfig context name in K8sSandboxEnvironmentConfig.
- Add automatic translation of Docker Compose files to Helm values files.
- Handle cancellation of evals (either manually or due to an error) such that Helm releases are uninstalled.
- Increase default Helm install timeout from 5 to 10 minutes.
- For "helm install timeout" errors, add link to docs within and include instructions on increasing timeout within the error message.
- Ignore "release not found" errors when uninstalling Helm charts (expected when helm release was not successfully installed).
- Prevent DNS exfiltration attacks by limiting which domains can be looked up (when using the built-in Helm chart).
- If a namespace is not includes in the kubeconfig context, default to a namespace named "default".
- Add `CLUSTER_DEFAULT` magic string for `runtimeClassName` which will remove the field from the pod spec.
- Add ignored `timeout_retry` parameter to `exec()` method.
- Always capture the output of `helm uninstall` so that errors can contain meaningful information.
- Add support for `inspect sandbox cleanup k8s` command to uninstall all Inspect Helm charts.
- Remove use of Inspect's deleted `SANDBOX` log level in favour of `trace_action()` and `trace_message()` functions.
- Initial release.
