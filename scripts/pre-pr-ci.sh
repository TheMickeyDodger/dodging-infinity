#!/usr/bin/env bash
# Local mirror of CI, for Herd delivery and anyone else, before commit and before push.
#
# Standing rule: never push a CI fix without a local green of the suite that failed.
# A docs change that is red in GitHub because of diScanFace was not verified by the
# tests that were run. This script runs the suites CI runs.
#
#   scripts/pre-pr-ci.sh                 Python, then Pixel, including the full UI suite
#   scripts/pre-pr-ci.sh --python-only   the Python runner, static, hermetic, compile, whitespace
#   scripts/pre-pr-ci.sh --pixel-only    Pixel types, lint, server tests, AsyncAPI, full UI suite
#
# The Python run is tests/_ci_runner.py with the same exclusions as
# .github/workflows/ci.yml, then the static suite, the hermetic Git suite,
# the compile line, and git diff --check. That includes the bound-constant
# pin list. Pixel includes `npm run asyncapi:generate` and `asyncapi:validate`
# (CI's generate step) and `npm run test:server`, which is where the fixture
# export checks live. The full UI suite is last because it is the long one.

set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

python_only=0
pixel_only=0
for arg in "$@"; do
  case "$arg" in
    --python-only) python_only=1 ;;
    --pixel-only) pixel_only=1 ;;
    *)
      echo "usage: scripts/pre-pr-ci.sh [--python-only | --pixel-only]" >&2
      exit 2
      ;;
  esac
done
if [ "$python_only" -eq 1 ] && [ "$pixel_only" -eq 1 ]; then
  echo "pass one of --python-only or --pixel-only, or neither" >&2
  exit 2
fi

run_python() {
  echo "== Python: unit runner (same exclusions as CI) =="
  python3 tests/_ci_runner.py --exclude test_static.py --exclude test_hermetic_git.py
  echo "== Python: static integration suite =="
  PYTHONPATH="$PWD" python3 tests/test_static.py
  echo "== Python: hermetic Git suite =="
  PYTHONPATH="$PWD" python3 tests/test_hermetic_git.py
  echo "== Python: compile =="
  python3 -m py_compile herdctl.py herdr/*.py tests/*.py codexgw.py codex_gateway/*.py tgop.py telegram_operator/*.py workflow_authority/*.py target_runtime/*.py dirun.py operator_session/*.py human_interaction/*.py durable_execution/*.py capability/*.py worker/*.py pr_delivery/*.py mission/*.py coordination/*.py mission_sources/*.py local_request/*.py direquest.py grok_bot/*.py grokbot.py tunnel_control/*.py ditunnel.py dicatalog.py dicatalog_http.py
  echo "== whitespace =="
  git diff --check "$(git hash-object -t tree /dev/null)"
}

run_pixel() {
  echo "== Pixel: install, types, lint, server tests, AsyncAPI, full UI suite =="
  cd "$root/integrations/pixel-agents"
  export HUSKY=0
  export CI=true
  npm ci --foreground-scripts --no-audit --no-fund --no-update-notifier
  npm run asyncapi:generate
  npm run asyncapi:validate
  if ! git diff --exit-code -- core/src/messages.ts; then
    echo "asyncapi:generate changed core/src/messages.ts; commit that file before pushing" >&2
    exit 1
  fi
  npm run check-types
  npm run lint
  npm run test:server
  echo "== Pixel: full UI suite (this is the long one) =="
  (cd webview-ui && npm test)
}

if [ "$pixel_only" -eq 0 ]; then
  run_python
fi
if [ "$python_only" -eq 0 ]; then
  run_pixel
fi

echo "pre-pr CI mirror: green"
