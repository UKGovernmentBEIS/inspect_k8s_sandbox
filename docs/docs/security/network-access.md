# Network Access

It is good security practice to prevent your containers from communicating with the
internet by default.

However, some evals may require internet access (e.g. to install packages or research
topics). The [built-in Helm chart](../helm/built-in-chart.md) allows you to specify a
list of domains that your containers can access.

## Cilium

The built-in Helm chart uses [Cilium](https://cilium.io/) Network Policies to restrict
network access.

Cilium has tooling to observe network requests, such as
[Hubble](https://github.com/cilium/hubble). Though note from the
[limitations](../design/limitations.md) section that domain names will not be shown when
using the built-in Helm chart due to how DNS resolution is handled.

See the [limitations](../design/limitations.md) section for how Cilium may make certain
Cyber misuse evals harder or impossible to solve.

### DNS Exfiltration

The built-in Helm chart prevents DNS exfiltration attacks. This is where an attacker
uses DNS lookups to an attacker-controlled domain or set of subdomains in order to
exfiltrate data from a system. For example, a malicious agent could make DNS requests
(which go via the kube-dns service if the Core DNS sidecar can't resolve them) to
hostnames like `somedata.attacker.com` and `somedata2.attacker.com` to exfiltrate data.

DNS lookups are restricted to only:
* Services within the Kubernetes namespace
* The domains specified in the `allowDomains` list in the `values.yaml` file.

See the [limitations](../design/limitations.md) section for how this may affect your
evals.

??? danger "I don't want to restrict DNS lookups"

    Please consider the security implications; DNS exfiltration will be possible **and**
    your containers will have unrestricted internet access.

    To allow **all** DNS queries and **all** internet access, set
    `allowDomains: ["*"]` or `allowEntities: "all"` in the `values.yaml` file.
