# Contributing Guide

**NOTE:** If you have any feature requests or suggestions, we'd love to hear about them
and discuss them with you before you raise a PR. Please come discuss your ideas with us
in our [Inspect
Community](https://join.slack.com/t/inspectcommunity/shared_invite/zt-2w9eaeusj-4Hu~IBHx2aORsKz~njuz4g)
Slack workspace.

## Development

This project uses [uv](https://github.com/astral-sh/uv) for Python packaging.

Run this beforehand:

```
uv sync --extra dev
```

You then can either source the venv with

```
source .venv/bin/activate
```

or prefix your pytest (etc.) commands with `uv run ...`

If you don't have access to a K8s cluster, you can develop using
[minikube](https://minikube.sigs.k8s.io/). If you're using VS Code, the devcontainer
(`.devcontainer`) will spin this up for you.

## Testing

This project uses [pytest](https://docs.pytest.org/en/stable/). To run all tests:

```bash
pytest
```

These tests are automatically run as part of CI. Some tests require a K8s cluster to be
available. To skip these tests:

```bash
pytest -m "not req_k8s"
```

## Linting & Formatting

[Ruff](https://docs.astral.sh/ruff/) is used for linting and formatting. To run both
checks manually:

```bash
ruff check .
ruff format .
```

These checks are automatically run as part of CI and pre-commit hooks.

## Type Checking

[Mypy](https://github.com/python/mypy) is used for type checking. To run type checks
manually:

```bash
mypy .
```

## Pre-commit Hooks and Continuous Integration

[pre-commit](https://pre-commit.com/) is used to maintain file formatting consistency
and code quality.

Installing the pre-commit hooks locally is not mandatory, but it is recommended.

```bash
pre-commit install
```

So long as the checks pass, feel free to use alternative tooling locally.

To run these checks manually:

```bash
pre-commit run --all-files
```

These hooks are automatically run as part of CI. When run in CI, no changes are made to
your code; the check simply fails.

## Documentation

Consider using the recommended [Rewrap](https://stkb.github.io/Rewrap/) extension
(`.vscode/extensions.json`) for VS Code to wrap Markdown text at 88 characters.

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
