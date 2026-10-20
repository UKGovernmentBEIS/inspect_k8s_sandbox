# Context

In December 2024, UK AISI migrated to a new K8s Cluster whilst also migrating some evals
from Docker to K8s.

The agentic evals in question required both internet access and access to services
deployed as part of the eval, resolved via DNS (e.g. a `victim` web server). These evals
used the built-in Helm chart which uses Cilium Network Policies (CNP). The Helm chart
also uses a release-scoped DNS service to resolve domain names like `victim` to the
relevant Pod deployed as part of the eval.

We observed Cilium dropping packets which ought to have been allowed by the Network
Policies. For example, queries to `wikipedia.org` would be dropped even if the
`allowDomains` field in `helm-values.yaml` was set to `["*"]`. The behaviour observed
was the request e.g. `curl` simply timed out. There may have also been issues accessing
eval-specific services like a `victim` web server.

This simple Inspect eval was used to measure the impact of the issue and evaluate
potential mitigations and solutions.


## Usage

```bash
python run.py
```

The scores will start to be computed after ~5 minutes (see `post_curl_sleep`).

The mean score will be reported when the eval finishes. A score of 1.0 indicates that
all `curl` commands succeeded, implying that both DNS and the HTTP requests were
successful.

Try adjusting values such as epochs, resources in `helm-values.yaml` (to control the
number of Pods per Node), uncommenting the readinessProbe, switching from allowDomains
to allowEntities.


## Expectations

Once the issue was resolved (which we suspect was due to the interaction between CNPs at
the cluster level with the CNPs at the eval level resulting in many Cilium
regenerations), the mean score is expected to be 1.0 without needing any changes to the
`helm-values.yaml` file.

When we were observing issues, the scores were ~0.6-0.8.
