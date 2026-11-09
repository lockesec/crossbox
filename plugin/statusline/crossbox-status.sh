#!/bin/sh
# Claude Code status line entry: "cbx: 3 ready · 1 claimed by me".
# A plugin cannot set statusLine; see docs/plugin.md for the settings.json snippet.
exec python3 "$(dirname "$0")/crossbox_status.py"
