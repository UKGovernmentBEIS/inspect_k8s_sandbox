# Repo guidance for coding agents

## Changelog (`CHANGELOG.md`)

New work goes under the `## Unreleased` heading. Each entry is
read by a **user of this library** — someone running `inspect eval` against the k8s
sandbox or writing Helm/compose config. Write for them, not for a reviewer of the diff.

Rules:

- **Describe what the user experiences, not how it's implemented.** The mechanism
  (worker threads, WebSocket frame sizes, `_preload_content`, deserialization paths)
  belongs in the PR, not the changelog. The user only cares about the observable change.
- **Cut the non-actionable.** If you're explaining *why* an obvious change is good, or
  *how* it works internally, cut it.
- **Keep what's actionable**: new config names, breaking-change migration steps,
  version requirements, the symptom a bug fix removes, public API names (exception
  types callers catch).
- **Don't churn existing entries.** When editing, make changes that are necessary
  (accuracy, completeness) and minimal. Don't swap synonyms or reorder for taste.

Examples (all from real entries):

```
# Internal mechanism — the user can't act on "raw API JSON":
- Parse pod reads from the raw API JSON instead of the kubernetes client's model
  deserialization, which serialized under high concurrency and caused TimeoutErrors...
# The user-facing effect:
- Fix `TimeoutError`s in high-concurrency evals (many concurrent clusters).
```

[#211](https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/pull/211)

```
# Trailing justification of a self-explanatory change:
- Include the cause's type and message in `K8sError`'s string, so callers reading
  only str(error) can tell a transient infra error from a real failure.
# Just the change:
- Include the cause's type and message in `K8sError`'s string.
```

[#199](https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/pull/199)

```
# Leads with implementation:
- Propagate the caller's context into the pod-operation worker thread so Inspect
  sandbox config overrides are honoured.
# Leads with the effect:
- Honour Inspect sandbox config overrides (e.g. exec output size limits) that were
  previously ignored on Kubernetes.
```

[#201](https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/pull/201)

Detail is warranted when it's actionable. A breaking change earns its length because
the user must reconfigure:

```
- **BREAKING CHANGE**: `allowDomains` egress is now restricted to ports 80/443, with
  the request identity enforced (TLS SNI on 443, HTTP `Host` on 80) rather than just
  the resolved IP. Wildcard entries require Cilium >= 1.18. New `allowDomainsPorts`
  opens other ports to those domains (IP-pinned; see `values.yaml`).
```

[#208](https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/pull/208)

A new config option keeps the "when would I use this" clause that distinguishes it from
existing options:

```
- Add a per-service `x-inspect_k8s_sandbox.resources` compose extension (alias `x-k8s`)
  for Kubernetes resource `requests`/`limits` (e.g. `ephemeral-storage`) that the
  `mem_limit`/`cpus`/`deploy.resources` shortcuts cannot express.
```

[#207](https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/pull/207)
