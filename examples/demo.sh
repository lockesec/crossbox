#!/bin/sh
# Play the README's two-worktree walkthrough in a throwaway directory.
#
#   sh examples/demo.sh            # needs `crossbox` and `git` on PATH
#   KEEP=1 sh examples/demo.sh     # leave the demo directory behind to poke at
#
# Nothing outside the temp directory is touched: the machine name, the
# rate-gate state and the git identity are all set for this script alone, and
# git ignores your own configuration (a signing or hook setting cannot break it).
# Exits non-zero if any step does not behave as the README says.
set -eu

command -v crossbox >/dev/null 2>&1 || { echo "demo: crossbox is not on PATH (pip install .)" >&2; exit 1; }

DEMO=$(mktemp -d "${TMPDIR:-/tmp}/crossbox-demo.XXXXXX")
if [ -z "${KEEP:-}" ]; then trap 'rm -rf "$DEMO"' EXIT; fi
export CROSSBOX_MACHINE=laptop
export CROSSBOX_RATE_STATE="$DEMO/rate-gate.json"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.invalid
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.invalid

say() { printf '\n# %s\n' "$*"; }
show() {
    printf '$'
    for a in "$@"; do
        case $a in *' '*) printf ' "%s"' "$a" ;; *) printf ' %s' "$a" ;; esac
    done
    printf '\n'
}
run() { show "$@"; "$@"; }

git init -q --bare -b main "$DEMO/origin.git"
git clone -q "$DEMO/origin.git" "$DEMO/app" 2>/dev/null
cd "$DEMO/app"
git commit -q --allow-empty -m init
git push -q origin HEAD:main 2>/dev/null

say "One task, three steps: b waits on a, c needs a GPU this laptop lacks"
show crossbox init; crossbox init >/dev/null
cat > .crossbox/config.yaml <<'EOF'
prefix: TASK
machines:
  laptop:      {provides: [docker]}
  workstation: {provides: [gpu, docker]}
EOF
run crossbox new "Parser rewrite" -d api -p high
run crossbox add TASK-API-001 "Write tests for the new grammar"
run crossbox add TASK-API-001 "Port the parser" -D a
run crossbox add TASK-API-001 "Benchmark on the GPU box" -t gpu -D b
git add -A && git commit -qm queue && git push -q 2>/dev/null
run crossbox ready

say "Claim step a and commit the claim"
run crossbox claim TASK-API-001.a
git add -A && git commit -qm "claim a"

say "A second agent in a second worktree sees the claim without a push"
git worktree add -q -b feature ../app-feature
cd ../app-feature
run crossbox ready
run crossbox add TASK-API-001 "Document the grammar"
git commit -qam "doc step"

say "Back in the first worktree, minting step d again is refused"
cd ../app
show crossbox add TASK-API-001 "Fix the lexer"
if crossbox add TASK-API-001 "Fix the lexer"; then
    echo "demo: FAILED: the origin guard let a duplicate step id through" >&2
    exit 1
fi
echo "(exit 1, as expected)"

say "Merge the other branch first, then the add goes through as e"
git merge -q feature
run crossbox add TASK-API-001 "Fix the lexer"
run crossbox done TASK-API-001.a
run crossbox release --all-mine
run crossbox status

if [ -n "${KEEP:-}" ]; then printf '\ndemo left in %s\n' "$DEMO"; fi
