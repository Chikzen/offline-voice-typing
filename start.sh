#!/bin/sh
# Linux (X11) launcher - the counterpart of start_console.cmd.
cd "$(dirname "$0")" || exit 1
exec venv/bin/python dictate.py "$@"
