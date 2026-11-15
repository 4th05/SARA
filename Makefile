PYTHON ?= $(CURDIR)/.venv/bin/python
UI_ARGS ?=

export LANGSMITH_TRACING := false
export MPLCONFIGDIR := $(CURDIR)/.local/matplotlib
export HF_HOME := $(CURDIR)/.local/models/huggingface

.PHONY: ui stop_ui

ui:
	@"$(PYTHON)" -m gateway.run $(UI_ARGS)

stop_ui:
	@"$(PYTHON)" -m gateway.stop
