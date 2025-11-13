REBUILD ?= false
test-deps:
	npm install -g @devcontainers/cli@v0.30.0
	devcontainer up \
		--workspace-folder=. \
		$(if $(filter true,$(REBUILD)),--remove-existing-container)

VERBOSE ?= false
TEST_PATH ?= test
test: test-deps
	devcontainer exec \
		--workspace-folder=. \
		uv run pytest \
			$(TEST_PATH) \
			-rA \
			-x \
			--color=yes \
			$(if $(filter true,$(VERBOSE)),-vvv)

.PHONY: test test-deps
