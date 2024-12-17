# Examples

## Hello World

Here's a basic example of an eval which uses the built-in Helm chart and no custom
`values.yaml` files.

```py
from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import bash


@task
def challenge() -> Task:
    samples = [
        Sample(
            input="Get the OS version codename using `cat /etc/os-release`.",
            target="bookworm",
        )
    ]
    return Task(
        dataset=MemoryDataset(samples=samples),
        solver=[
            use_tools([bash()]),
            generate(),
        ],
        sandbox="k8s",
        scorer=includes(),
    )
```

If this were in a `task.py` file, run it with `inspect eval task.py`.

## Custom values.yaml

```py
return Task(
    ...,
    sandbox=("k8s", "values.yaml"),
)
```

Assuming you're using the built-in Helm chart, a suitable `values.yaml` file is:

```yaml
default:
  image: ubuntu:24.04
  command: ["tail", "-f", "/dev/null"]
```

## Additional infrastructure

Again, assuming you're using the built-in Helm chart. The Nginx server will be
addressable at `nginx:80` and `my-web-server.com:80` from any of the containers in your
Helm release.

```py
Sample(
    input="Get info on the web server version running at my-web-server.com.",
    target="nginx/1.27.0",
)
```

```yaml
default:
  image: ubuntu:24.04
  command: ["tail", "-f", "/dev/null"]
server:
  image: nginx:1.27.0
  dnsRecord: true
  additionalDnsRecords:
    - "my-web-server.com"
  readinessProbe:
    tcpSocket:
      port: 80
```
