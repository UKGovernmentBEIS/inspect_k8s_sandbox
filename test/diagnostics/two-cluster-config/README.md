# Context

When running an Inspect eval inside a Kubernetes pod that has **both** in-cluster config
and a mounted kubeconfig pointing at a different cluster (METR's deployment pattern),
`k8s_sandbox` must prefer the kubeconfig. Prior to PR #177, `_Config._load()` tried
`load_incluster_config()` first, which caused sandbox pods to target the runner cluster
instead of the intended sandbox cluster.

This diagnostic reproduces that two-cluster scenario using two minikube clusters:

- **runner** -- minimal cluster, deliberately NOT set up for sandbox workloads. Runs a pod
  containing the test eval with both in-cluster config and a mounted kubeconfig.
- **sandbox** -- fully provisioned (runc RuntimeClass, nfs-csi StorageClass, Cilium CRDs).
  This is where sandbox pods should land.

With the kubeconfig-first fix applied, `load_kube_config()` succeeds first and the eval
targets the sandbox cluster.


## Usage

```bash
bash run.sh
```

The script takes approximately 5 minutes. It creates both minikube clusters, builds a test
image, runs a Job in the runner cluster, and reports PASS/FAIL. Both clusters are cleaned up
on exit (including on failure).

Prerequisites: `minikube`, `docker`, `kubectl`, `cilium` must be on PATH.


## Expectations

**PASS**: The eval completes successfully with accuracy 1.0, confirming that the kubeconfig
was preferred over in-cluster config and sandbox pods landed in the correct cluster.

**FAIL**: Either `_Config.in_cluster` is `True` (kubeconfig was not preferred) or the
Inspect eval fails (sandbox pods targeted the wrong cluster).
