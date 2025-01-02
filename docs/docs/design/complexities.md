# Complexities

## `exec()`

The behaviour of `kubectl exec` is not consistent with that in `docker exec`. Consider
the following command (note: the `k8s_sandbox` package does not actually use `kubectl`,
but it illustrates the point).

```sh
kubectl exec pod   -- bash -c "python server.py &"
docker exec container bash -c "python server.py &"
```

Kubernetes won't consider the command completed until the Python process exits, whereas
Docker will consider the command completed as soon as the bash script exits.

More specifically, Kubernetes will wait for the stdout and stderr file descriptors to be
closed (including by any child processes which inherited them).

The `kubectl` command could be re-written like so to make it behave in the same way as
`docker exec`:

```sh
kubectl exec pod -- bash -c "python server.py > /dev/null 2>&1 &"
```

However, we do not have control over the commands which LLMs choose to run, so the
`k8s_sandbox` package attempts to emulate the Docker behaviour (which seems more
intuitive anyway).

See the source code for documentation on how this is achieved.

!!! question "Why not use `tty=True` (`-t`)?"

    Whilst this would give us the behaviour we want around commands containing a
    backgrounded task (`&`), it means that stderr is redirected to stdout. It also
    changes the line endings of the output from `\n` to `\r\n`, which means that the
    output is not consistent with output from other sandbox environments like Docker.
