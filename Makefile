PYTHON ?= $(CURDIR)/.venv/bin/python
UI_ARGS ?=
GATEWAY_HOST ?=
.DEFAULT_GOAL := ui

export LANGSMITH_TRACING := false
export MPLCONFIGDIR := $(CURDIR)/.local/matplotlib
export HF_HOME := $(CURDIR)/.local/models/huggingface

.PHONY: setup models notebooks cert ui stop_ui

setup:
	@./tools/setup.sh

models:
	@"$(PYTHON)" -m gateway.download_models

notebooks:
	@"$(PYTHON)" -m pip install -r gateway/requirements-notebook.txt

cert:
	@if [ -z "$(GATEWAY_HOST)" ]; then \
		printf '%s\n' "Use make cert GATEWAY_HOST=your-gateway-IP"; \
		exit 1; \
	fi
	@"$(PYTHON)" -m gateway.make_certificate --host "$(GATEWAY_HOST)"

ui:
	@"$(PYTHON)" -m gateway.run $(UI_ARGS)

stop_ui:
	@"$(PYTHON)" -m gateway.stop
