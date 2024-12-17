# Network Access

It is good security practice to prevent your containers from communicating with the
internet by default.

However, some evals may require internet access (e.g. to install packages or research
topics). The [built-in Helm chart](../helm/built-in-chart.md) allows you to specify a list
of domains that your containers can access.

## Cilium

The built-in Helm chart uses [Cilium](https://cilium.io/) Network Policies to restrict
network access.

Cilium has tooling to observe network requests, such as
[Hubble](https://github.com/cilium/hubble). Though note from the
[limitations](../design/limitations.md) section that domain names will not be shown when
using the built-in Helm chart due to how DNS resolution is handled.

See the [limitations](../design/limitations.md) section for how Cilium may make certain
Cyber misuse evals harder or impossible to solve.
