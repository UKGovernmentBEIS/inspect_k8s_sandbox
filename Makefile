.PHONY: test test-parallel test-unit test-integration

# Run all tests sequentially (current behavior)
test:
	uv run pytest -rA --color=yes

# Run all tests in parallel (unit tests fully parallel, then integration with limited concurrency)
test-parallel: test-unit test-integration

# Run unit tests (non-K8s) with full parallelization
test-unit:
	uv run pytest -rA --color=yes -n auto -m "not req_k8s"

# Run K8s integration tests with limited concurrency
test-integration:
	uv run pytest -rA --color=yes -n 5 -m "req_k8s"
