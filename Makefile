.PHONY: check integration eval

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest -m "not integration and not eval" -q

integration:
	uv run pytest -m integration -q
	cd tools/goinspect && go test ./...

# --env-file hands .env to pytest alone, so the key never enters the shell's environment.
# -rP prints what passing tests printed, the spend and the trace folder, through the key guard.
eval:
	uv run --env-file .env pytest -m eval -q -rP
