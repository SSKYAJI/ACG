#!/usr/bin/env bash
# Idempotent checkout of spring-attic/greenhouse pinned to the commit the human
# author build-spiked. Safe to re-run; the second invocation is a no-op once
# the working tree is at $COMMIT.
set -euo pipefail

WORKDIR="${WORKDIR:-experiments/greenhouse/checkout}"
REPO="https://github.com/spring-attic/greenhouse.git"
COMMIT="174c1c320875a66447deb2a15d04fc86afd07f60"

if [ ! -d "$WORKDIR" ]; then
  git clone --depth 1000 "$REPO" "$WORKDIR"
fi

cd "$WORKDIR"
git fetch --depth 1000 origin "$COMMIT" 2>/dev/null || true
git checkout "$COMMIT"
echo "Greenhouse pinned at $COMMIT"
