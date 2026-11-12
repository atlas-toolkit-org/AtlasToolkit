#!/bin/sh
# Start Atlas Toolkit from source (macOS / Linux).
exec "$(dirname "$0")/../.venv/bin/python" -m atlas_toolkit "$@"
