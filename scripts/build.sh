#!/bin/sh
set -eu

# Upgrade dependencies.
uv lock --upgrade
uv sync --dev --locked

# Apply safe fixes and formatting.
uv run --locked ruff check --fix app
uv run --locked ruff format app

# Build the wheel and source distribution.
uv build
