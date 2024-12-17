# Hubble (Cilium UI)

If you're using the built-in Helm chart, [Cilium](https://cilium.io/) is being used for
network policy enforcement. Cilium includes a UI called
[Hubble](https://docs.cilium.io/en/stable/observability/hubble/hubble-ui/) which can be
used to monitor network traffic.

![hubble](images/hubble.png)

This is particularly useful for curating a list of allowed domain names for your evals.

!!! note "Prerequisites"

    You'll need access to the namespace in which `hubble-ui` is deployed (`kube-system`
    by default).

    You'll need to [enable the Hubble UI](https://docs.cilium.io/en/stable/observability/hubble/hubble-ui/#enable-the-hubble-ui)
    in your Cilium installation if it's not already enabled.


To access Hubble, [install the `cilium`
CLI](https://docs.cilium.io/en/stable/gettingstarted/k8s-install-default/#install-the-cilium-cli)
on your development machine and run:

```sh
cilium hubble ui
```

Then you can access Hubble in your browser at
[http://localhost:12000](http://localhost:12000).

!!! bug "Stuck on the loading page?"

    Try changing the port number. To forward to a random port number:

    ```sh
    cilium hubble ui --port-forward 0
    ```
