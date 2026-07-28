# Kubernetes Workloads

Inspect sandbox containers run untrusted code. Their Kubernetes identities and the
cluster workloads they are allowed to create should therefore be treated as untrusted
too.

## Service accounts

The built-in Helm chart disables Kubernetes service-account token automount by default.
Selecting an existing ServiceAccount with `serviceAccountName` does not expose its
Kubernetes API token unless `automountServiceAccountToken` is also set to `true`. This
keeps Kubernetes API credentials separate from provider-specific workload identity
tokens such as IRSA. Set `serviceAccountCreate: true` only when the Helm release should
create and own the account.

If a sandbox needs Kubernetes API access:

- Pre-create a dedicated ServiceAccount with only the required RBAC permissions.
- Do not use the namespace's default ServiceAccount for an in-cluster Inspect, Helm, or
  operator deployment.
- Do not share a controller ServiceAccount with sandbox pods.
- Permit network access to the API server only when it is required.
- Remember that concurrent samples selecting the same ServiceAccount share one
  Kubernetes identity.

See the [built-in chart configuration](../helm/built-in-chart.md#service-accounts-and-kubernetes-api-access)
for the explicit opt-in.

## Admission control

RBAC controls which Kubernetes resources an identity may create, but it does not make a
created pod safe. A ServiceAccount that can create workloads may be able to request a
privileged container or mount the node filesystem with `hostPath`.

Use admission policy to reject privileged workloads, `hostPath`, host namespaces, and
other node-level access in sandbox namespaces. Where your images and workload settings
are compatible, enforce Kubernetes' [Restricted Pod Security
Standard](https://kubernetes.io/docs/concepts/security/pod-security-standards/).
