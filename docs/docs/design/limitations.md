# Limitations

## Containers may restart

Containers may restart during an eval. This can be for several reasons including:

* The container terminates or crashes (PID 1 exited).
* The Pod is killed by Kubernetes (e.g. Out Of Memory).
* The Pod is rescheduled by Kubernetes (e.g. due to node failure or resource
  constraints).
* The Pod's [liveness
  probes](https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/#liveness-probe)
  fail.

Allowing containers to restart may be desirable:

* You may not want an agent to be able to deliberately crash its container (`kill 1`) in
  order to fail an eval if that would result in retrying the eval.
* If an agent causes your support infrastructure (like a web server) to crash or exceed
  memory limits, you may want it to restart.
* Your containers may depend on a certain startup order e.g. a web server assumes it can
  connect to a database which hasn't been scheduled or is not ready yet. In which case
  you would want the web server to enter a crash backoff loop until the database is
  available.

Sometimes, containers restarting is not desirable:

* If state is stored in-memory or on a non-persistent volume, it will be lost. E.g. an
  agent starts a long-running background process in its container or a web server stores
  session data in-memory.

If the eval attempts to directly interact with a container whilst it is restarting (e.g.
an agent tries to `exec()` a shell command), that sample of the eval will fail with a
suitable exception.

You can reduce the likelihood of Pod eviction by setting the resource limits and
requests of Pods such that you get a `Guaranteed` [QoS
class](https://kubernetes.io/docs/tasks/configure-pod-container/quality-service-pod/)
which is the case by default in the [built-in Helm
chart](../helm//built-in-chart.md#resource-requests-and-limits).

You can reduce the impact of a container restarting by using persistent volumes.

??? question "Why not use Jobs over StatefulSets?"

    Instead of using
    [StatefulSets](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
    or
    [Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/),
    [Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/) could be used
    as the workload controller for the underlying Pods. This way, the Pod's
    `restartPolicy` can be configured as `Never` and the Job's `backoffLimit` as `0` in
    the cases where restarts are not desirable. However, this introduces some
    complexities:

    1. The `--wait` flag passed to `helm install` does not wait for Pods belonging to
    Jobs to be in a Running state. We'd have to implement our own waiting mechanism,
    possibly as a Helm post-install hook to avoid coupling the Python code to the Helm
    chart.

    2. We either need to ask developers to write their images in a way which won't crash
    if dependencies are not ready, or provide some way of expressing dependencies
    (e.g. a `dependsOn` field in the Helm chart) and ensuring the Pods are started in
    that order (e.g. with an init container which queries `kubectl`).

    3. The Python code would need a way of periodically checking (e.g. before every
    `exec()`) if any Pods in the release are in a failed state and won't be restarted,
    then fail that sample of the eval by raising an exception.


    What about bare Pods?

    When using bare Pods (i.e. not managed by a workload controller),
    `helm install --wait` will wait for all Pods to be in a Running state. However, if
    a Pod enters a failed state, it will not be restarted and `helm install` will wait
    indefinitely.


## Denied network requests behaviour

When Cilium denies a network request, it simply drops the packet.

If you're using the built-in Helm chart, [DNS lookups are
restricted](../security/network-access.md#dns-exfiltration) in addition to direct
network requests.

Therefore, when an agent tries to access a blocked resource via a client like `wget` or
`curl`, one of two things will happen:

1. The DNS lookup (if relevant) will time out. In this case, the client may error with
  "temporary failure in DNS resolution" or similar. If the client is not configured with
  a timeout (e.g. `host -r`), it may hang indefinitely until the tool call times out
  (see below).
2. If there is no DNS lookup required, the client will hang waiting for a response until
  its timeout (if it has one) is reached.

We recommend that any tools you define or use pass the `timeout` parameter (e.g.
`timeout=60`) in case the model runs a command that doesn't have a built-in timeout.


## Reverse DNS lookups aren't supported

When using the built-in Helm chart, reverse DNS lookups (i.e. looking up the hostname
from an IP) are not supported. This is because DNS lookups are restricted to prevent
[DNS exfiltration](../security/network-access.md#dns-exfiltration).

??? question "Why is ping slow?"

    Executing commands like

    ```sh
    ping victim
    ```

    where `victim` is another service's name will result in a reverse DNS lookup in
    addition to the forward DNS lookup. The reverse lookup will fail (which may add ~5s
    to the execution time of the command) but the forward lookup and subsequent command
    will succeed. `ping -n` disables reverse DNS lookups. Commands such as `curl` and
    `wget` do not perform reverse DNS lookups.


## Cilium's security measures prevent some exploits

Cilium imposes some sensible network security measures, described on their
[blog](https://cilium.io/blog/2020/06/29/cilium-kubernetes-cni-vulnerability/). Amongst
them is packet spoofing prevention. Any evals (e.g. Cyber misuse) which depend on the
agent spoofing packets may not work.

## The CoreDNS sidecar in the built-in Helm chart will use port 53

Evals which require the use of port 53 (e.g. a Cyber eval with a vulnerable DNS server)
will not work with the built-in Helm chart as each Pod has a CoreDNS sidecar which uses
port 53.

## Changes to `/etc/hosts` are ignored

The kubelet manages the `/etc/hosts` file. Any changes manually made to this file e.g.
in the Dockerfile:

```Dockerfile
echo "127.0.0.1 my-domain.com" >> /etc/hosts
```

may be lost. If your aim is to make certain hostnames resolve to one of your services,
use the `additionalDnsRecords` field in the [built-in Helm
chart](../helm/built-in-chart.md#dns). Or, if using a custom Helm chart, consider using
the `hostAliases` field in the Pod spec
([docs](https://kubernetes.io/docs/tasks/network/customize-hosts-file-for-pods/)).

## Transient network or infrastructure issues during `exec()` won't be retried

The following exceptions have occasionally been observed during calls to `exec()`,
`read_file()` or `write_file()`:

* `WebSocketBadStatusException: pod does not exist` (re-raised as `ApiException`)
* `WebSocketBadStatusException: container not found` (re-raised as `ApiException`)
* `WebSocketBadStatusException: Handshake status 500 Internal Server Error` (re-raised
  as `ApiException`)
* `WebSocketConnectionClosedException: Connection to remote host was lost`
* `SSLEOFError: EOF occurred in violation of protocol`

These are likely due to transient network or infrastructure issues. For example, when a
node becomes unhealthy and the Pod is rescheduled.

The `k8s_sandbox` package will not retry the remote command execution when any of these
(or other) exceptions are raised because it cannot assume that the command is idempotent
and that the command did not at least start executing. This will result in that sample
of the eval failing.

## The `user` parameter to `exec()` is not supported

In Kubernetes, a container runs as a single user. If you need to run commands as
different users, you may have to run the container as root and use a tool like `runuser`
to run commands as different users.

## Images are not automatically built, tagged or pushed

The process of building, tagging and pushing images is left to the user or other tooling
as it is highly dependent on your environment and practices.

## `TimeoutError` won't be raised on busybox images

The `timeout` binary on busybox images behaves differently, causing a 128 + 15 (SIGTERM)
= 143 exit code rather than a 124 exit code. This will result in a suitable `ExecResult`
being returned rather than raising a `TimeoutError`.

## Service names must be lower case alphanumeric

In the built-in Helm chart, service names (i.e. the keys in the `services` dict) must
match the case-sensitive regex `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` e.g. `my-name` or
`123-abc`. The Helm chart will fail to install if this is not the case.
