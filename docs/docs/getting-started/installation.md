# Installation

To make the K8s sandbox environment provider discoverable to Inspect, install this
Python package in your environment.


=== "pip"

    ```sh
    pip install git+https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox.git
    ```

=== "poetry"

    ```sh
    poetry add git+https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox.git
    ```

=== "uv"

    ```sh
    uv add git+https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox.git
    ```

Then, pass `"k8s"` as the `sandbox` argument to the Inspect `Task` or `Sample`
constructor.

```py
return Task(
    ...,
    sandbox="k8s",
)
```
