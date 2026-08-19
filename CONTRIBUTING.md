# Contributing Guide

**NOTE:** If you have any feature requests or suggestions, we'd love to hear about them
and discuss them with you before you raise a PR. Please come discuss your ideas with us
in our [Inspect
Community](https://join.slack.com/t/inspectcommunity/shared_invite/zt-2w9eaeusj-4Hu~IBHx2aORsKz~njuz4g)
Slack workspace.

## Before you open a PR

This provider is a thin layer over infrastructure we don't control — Kubernetes, Helm
and Cilium. Most bugs in it are claims about what that infrastructure does at runtime,
and those are cheap to get wrong from reading documentation or upstream source.

If your change asserts a runtime behaviour, we need the observation, not the derivation.

- **Run it against a real cluster and paste what you saw.** minikube is fine (see
  below); the `req_k8s` marker identifies the tests that need a cluster. A test without
  that marker is not evidence about runtime behaviour, however green it is.
- **Show the negative control.** Give the result with your change and without it. If you
  can't produce a run that fails on `main` and passes on your branch, say so and explain
  why.
- **If you can't run it, open an issue rather than a PR.** Reasoning, upstream source
  links and a proposed patch are all welcome in an issue. A confidently argued wrong
  premise costs more than no PR at all, because it has to be disproved before it can be
  declined.

The trap specific to this repo is the Helm render tests. They assert what YAML we emit,
not what the cluster enforces: two manifests that render differently can enforce
identically, and two that look equivalent can behave differently. A render test can't
settle an enforcement question in either direction.

A sufficient experiment looks like: baseline without the change, the behaviour with it,
then baseline again to show the effect went away — several attempts per phase, plus a
positive control proving the test could have observed a difference if there were one.

## Getting started

This project uses [uv](https://github.com/astral-sh/uv) for Python packaging.

Run this beforehand:

```
uv sync --extra dev
```

The commands below are written as `uv run --extra dev ...`, which works whether or not
the venv is activated (`--extra dev` is needed because the dev tools are an optional
extra here). Drop the prefix if you'd rather activate the venv:

```
source .venv/bin/activate
```

If you don't have access to a K8s cluster, you can develop using
[minikube](https://minikube.sigs.k8s.io/). If you're using VS Code, the devcontainer
(`.devcontainer`) will spin this up for you.

## Testing

This project uses [pytest](https://docs.pytest.org/en/stable/). To run all tests:

```bash
uv run --extra dev pytest
```

(AISI users: first `unset INSPECT_TELEMETRY INSPECT_API_KEY_OVERRIDE INSPECT_REQUIRED_HOOKS`)

These tests are automatically run as part of CI. Some tests require a K8s cluster to be
available. To skip these tests:

```bash
uv run --extra dev pytest -m "not req_k8s"
```

### Test Timeouts

K8s tests use a 90-second Helm timeout (default is 10 minutes) configured in
`pyproject.toml` via `INSPECT_HELM_TIMEOUT=90`. Assuming you're using a cluster
that isn't overloaded, this should be adequate.

Override if needed:

```bash
INSPECT_HELM_TIMEOUT=300 uv run --extra dev pytest
```

## Linting & Formatting

[Ruff](https://docs.astral.sh/ruff/) is used for linting and formatting. To run both
checks manually:

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format .
```

These checks are automatically run as part of CI and pre-commit hooks.

## Type Checking

[Mypy](https://github.com/python/mypy) is used for type checking. To run type checks
manually:

```bash
uv run --extra dev mypy .
```

## Pre-commit Hooks and Continuous Integration

[pre-commit](https://pre-commit.com/) is used to maintain file formatting consistency
and code quality.

Installing the pre-commit hooks locally is not mandatory, but it is recommended.

```bash
uv run --extra dev pre-commit install
```

So long as the checks pass, feel free to use alternative tooling locally.

To run these checks manually:

```bash
uv run --extra dev pre-commit run --all-files
```

These hooks are automatically run as part of CI. When run in CI, no changes are made to
your code; the check simply fails.

## Documentation

Consider using the recommended [Rewrap](https://stkb.github.io/Rewrap/) extension
(`.vscode/extensions.json`) for VS Code to wrap Markdown text at 88 characters.

## Changelog

If appropriate, add an entry under the `## Unreleased` heading in `CHANGELOG.md` when
submitting a PR. Create that heading if the last release consumed it.

Entries under a dated release heading are published history — don't add to or edit
them. In particular, if a release is cut after you branch, a stale branch can silently
land your entry in the just-released section (the release commit renames `##
Unreleased` to the dated heading, so your diff still applies): after rebasing onto
`main`, check your entry still sits under `## Unreleased`.

## Releasing

Releases are published manually using uv's standard
[build](https://docs.astral.sh/uv/guides/package/#building-your-package) and
[publish](https://docs.astral.sh/uv/guides/package/#publishing-your-package) flow —
this guide does not duplicate those steps.

The repo-specific parts are:

- Bump `version` in `pyproject.toml` and run `uv lock` to update `uv.lock`. Set the
  bundled `agent-env` chart version in `Chart.yaml` to match.
- Replace the `## Unreleased` heading in `CHANGELOG.md` with `## <YYYY-MM-DD> <version>`
  (see existing entries for the format).
- After merging, tag the release commit `vX.Y.Z` and push the tag.

## Conventions

### Package Structure and API Visibility

The Python packages, modules and members follow a similar API visibility naming
convention to that used in the [inspect_ai](https://inspect.aisi.org.uk/) package.

Public API members (e.g. classes, functions, constants) are exported in the package's
`__init__.py` file. Members are exported rather than modules (i.e. .py files) to avoid
all of the module's imports also being implicitly exported.

Module-private members are prefixed with an underscore `_`. These members are not
intended for use outside of the module in which they are defined (except in tests).

Class-private members are prefixed with an underscore `_`. These members are not
intended for use outside of the class in which they are defined (except in tests). We
don't use double underscores `__`  which is consistent with [Google's Python style
guide](https://google.github.io/styleguide/pyguide.html).

Non-public modules (i.e. .py files) are prefixed with an underscore `_` (unless a parent
package is already prefixed with an underscore).

### Test Structure

When writing tests, please endeavour to follow the Arrange-Act-Assert (AAA) pattern.
This pattern helps create clear and readable tests by separating the test into three
distinct sections:

1. Arrange: Set up the test data and conditions.
2. Act: Perform the action being tested.
3. Assert: Verify the results.

Each section should be separated by one blank line. Here's an example. The comments are
for illustrative purposes only and do not need to be included in the test code.

```python
def test_abs_with_negative_number():
    # Arrange
    negative = -5

    # Act
    actual = abs(negative)

    # Assert
    assert actual == 5
```

There will of course be some exceptions to this pattern.
