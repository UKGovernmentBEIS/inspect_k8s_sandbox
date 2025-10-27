# Context

This is a harness, similar to the one in [network issues](../network-issues/README.md).

It's designed to reproduce the "too many open files" problem linked in this issue

https://github.com/UKGovernmentBEIS/inspect_k8s_sandbox/issues/127

We use a custom helm chart because we do not need network access, and the default
sidecar container is a waste of resources and slows down the test spin-up.

The test spins up 500 containers and runs `sleep 1` in a loop.
This results in many calls to sandbox.exec, which opens lots of files.

## Usage

Ensure your ulimit has not already been increased.
For this to be a valid test, this should return `1024`.

```bash
ulimit -n
```

Now run the test. Note, this creates 500 helm charts and pods, so try to use a cluster
where nothing critical is currently happening. The test should take less than 5 minutes.


```bash
python run-open-files-test.py
```

## Expectations

The eval should complete without any errors. If there are problems with open files,
this will manifest as a crash, but you may need to wait for the helm uninstalls to
complete before deciphering the log output.
