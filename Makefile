.PHONY: perf-40 perf-40-dry

perf-40:
	bash infra/tests/run-40-user-validation.sh

perf-40-dry:
	bash infra/tests/run-40-user-validation.sh --dry-run
