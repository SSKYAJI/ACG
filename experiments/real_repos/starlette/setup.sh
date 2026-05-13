#!/usr/bin/env bash
# Pin Starlette checkout to the earliest manifest parent so PRs 3137/3148/3166
# stack on one timeline, refresh the ACG graph, and verify pytest collection
# using a dedicated venv inside the checkout (does not use the cognition .venv).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHECKOUT="$ROOT/experiments/real_repos/starlette/checkout"
# Earliest parent among manifest PRs 3137 / 3148 / 3166 (ancestor of the other parents).
PINNED_COMMIT="2b73aecd8377e0c189943a5f30d3dbab134f6104"
VENVDIR="$CHECKOUT/.venv"

DRY_RUN=0
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    DRY_RUN=1
  fi
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: $*"
    return 0
  fi
  "$@"
}

echo "Using repo root: $ROOT"
echo "Checkout: $CHECKOUT"
echo "Pinned commit: $PINNED_COMMIT"

run git -C "$CHECKOUT" fetch --all --quiet || true
run git -C "$CHECKOUT" checkout "$PINNED_COMMIT"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY-RUN: python3 -m venv \"$VENVDIR\"  # if missing"
  echo "DRY-RUN: \"$VENVDIR/bin/pip\" install -q -U pip"
  echo "DRY-RUN: (cd \"$CHECKOUT\" && \"$VENVDIR/bin/pip\" install -q -e \".[full]\" pytest trio httpx typing_extensions)"
  echo "DRY-RUN: \"$ROOT/.venv/bin/acg\" init-graph --repo \"$CHECKOUT\" --language python --rescan-graph"
  echo "DRY-RUN: \"$VENVDIR/bin/python\" -m pytest \"$CHECKOUT/tests/\" --collect-only -q"
  exit 0
fi

if [[ ! -x "$VENVDIR/bin/python" ]]; then
  python3 -m venv "$VENVDIR"
fi
"$VENVDIR/bin/pip" install -q -U pip
(
  cd "$CHECKOUT"
  "$VENVDIR/bin/pip" install -q -e ".[full]" pytest trio httpx typing_extensions
)

"$ROOT/.venv/bin/acg" init-graph --repo "$CHECKOUT" --language python --rescan-graph

"$VENVDIR/bin/python" -m pytest "$CHECKOUT/tests/" --collect-only -q
