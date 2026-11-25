import re
import shlex

from inspect_ai.util import SandboxUserUnsupportedError

EXEC_USER_URL = "https://k8s-sandbox.aisi.org.uk/design/limitations#exec-user"


def run_as(user: str | None, command: list[str]) -> list[str]:
    """Wrap `command` so that it runs as `user` in the container.

    Only reach for `runuser` if the container is not already running as that
    user. `runuser` calls setgroups(2), which needs CAP_SETGID even when
    switching root -> root, so an unconditional wrapper makes every switch fail
    in a container whose capabilities have been dropped. Skipping it is close to
    a no-op, but not exactly one: runuser also normalizes HOME, SHELL, USER,
    LOGNAME and PATH, which the skip path inherits from the container instead.

    The check runs in the container rather than here so that it costs no extra
    round trip. `sh -c` takes its script from argv, so stdin reaches `command`
    unread.

    `user` may be a name or a uid, and the two must be compared against
    different things: testing a uid against `id -un` (or a name against `id -u`)
    would match an account merely *named* "0" against uid 0 and run the command
    as the wrong user. So only the applicable test is emitted.

    `id -un` reports only the first passwd name for a uid, so a second name for
    the same uid (root/toor) is not recognised and falls through to `runuser`,
    which is the safe direction.
    """
    if user is None:
        return command
    if not user:
        # Matches nothing; leave runuser to reject it.
        return ["runuser", "-u", user, "--", *command]
    quoted = shlex.quote(user)
    joined = shlex.join(command)
    # A missing or failing `id` leaves the substitution empty, which cannot
    # equal a non-empty user, so that also falls through to `runuser`.
    current = "id -u" if user.isdigit() else "id -un"
    return [
        "/bin/sh",
        "-c",
        f'if [ "$({current} 2>/dev/null)" = {quoted} ]; then exec {joined}; fi; '
        f"exec runuser -u {quoted} -- {joined}",
    ]


def raise_for_unsupported_user(stderr: str, user: str) -> None:
    """Raise SandboxUserUnsupportedError if `run_as` could not switch to `user`.

    Only call this when the wrapped command failed. Each pattern is anchored on
    the `runuser: ` prefix because `newgrp`, `sg`, `su` and `login` print the
    same message bodies.
    """
    reasons = (
        (r"runuser: user \S+ does not exist", "does not exist in the container"),
        (
            r"runuser: may not be used by non-root users",
            "cannot be switched to: the container is not running as root",
        ),
        (
            r"runuser: cannot set groups",
            "cannot be switched to: the container lacks CAP_SETGID",
        ),
        (
            r"runuser: not found",
            "cannot be switched to: runuser is not installed in the container",
        ),
    )
    for pattern, reason in reasons:
        if re.search(pattern, stderr, re.IGNORECASE):
            raise SandboxUserUnsupportedError(
                f"User '{user}' {reason}. Docs: {EXEC_USER_URL}\n{stderr}"
            )
