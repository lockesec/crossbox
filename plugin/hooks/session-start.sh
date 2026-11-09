#!/bin/sh
# Claude Code SessionStart hook: show the agent what it can work on, here.
# The plugin registers it (plugin/hooks/hooks.json). To use it on its own:
#   "hooks": {"SessionStart": [{"hooks": [{"type": "command",
#             "command": "sh /path/to/session-start.sh"}]}]}
# Whatever this prints is added to the session's context, so keep it short.
# Silent unless the project has a .crossbox/ directory.
dir=${CLAUDE_PROJECT_DIR:-$PWD}
[ -d "$dir/.crossbox" ] || exit 0
cd "$dir" || exit 0
if ! command -v crossbox >/dev/null 2>&1; then
    echo "## crossbox"
    echo "NOT EVALUABLE: this project has .crossbox/ but the crossbox command is not on PATH (pip install git+https://github.com/lockesec/crossbox)."
    exit 0
fi
echo "## crossbox"
# A cloned repository's needs-registry probes are shell commands from that
# repository: opening it must not run them. CROSSBOX_ALLOW_PROBES=1 opts in.
# $probes is deliberately unquoted: empty means no argument at all.
probes=--no-probes
[ "${CROSSBOX_ALLOW_PROBES:-}" = 1 ] && probes=
# --root: the project may be a subdirectory of the repository holding .crossbox/
# shellcheck disable=SC2086
crossbox --root "$PWD" ready $probes 2>&1 | head -25   # stderr too: a NOT EVALUABLE reason must reach the agent
crossbox --root "$PWD" claims 2>/dev/null | tail -n 6
crossbox --root "$PWD" notes 2>/dev/null | tail -n 12
exit 0
