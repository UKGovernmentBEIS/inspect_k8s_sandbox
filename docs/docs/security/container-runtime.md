# Container Runtime

## gVisor

Using [gVisor](https://gvisor.dev/) as the container runtime wherever possible is
recommended for good security practice when running untrusted LLM-generated code in
containers. The built-in Helm chart uses `gvisor` as the container runtime class name
unless you specify otherwise (see [built-in chart](../helm/built-in-chart.md)).

There are however some differences in behaviour between using gVisor (which uses the
`runsc` runtime) and the default `runc` runtime. This may make some Cyber misuse evals
harder or impossible to solve.

gVisor blocks certain low-level system calls such as directly creating and sending
packets with `hashcat`.

gVisor may prevent certain security vulnerabilities from being exploited, such as
breaking out of chroot jails.

gVisor may prevent agents from using password-based SSH authentication using tools like
`sshpass`. They can still use key-based SSH authentication or password-based SSH
authentication using tools like `paramiko`.

!!! info

    To determine whether gVisor is being used as the runtime for a container or not,
    open a shell into the container and run `sudo dmesg | grep gvisor`. If there is a
    match, then gVisor is being used.
