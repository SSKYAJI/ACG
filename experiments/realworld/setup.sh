#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${ROOT}/checkout"
REPO="https://github.com/lujakob/nestjs-realworld-example-app.git"
# After first clone, run:
#   git -C experiments/realworld/checkout rev-parse HEAD
# and paste the full 40-char SHA below to replace the short ref.
COMMIT="c1c2cc4e448b279ff083272df1ac50d20c3304fa"

if [[ ! -d "${TARGET}/.git" ]]; then
  git clone "${REPO}" "${TARGET}"
fi

if [[ ! "${COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "[setup] Current HEAD:"
  git -C "${TARGET}" rev-parse HEAD
  echo "[setup] Replace COMMIT with the full 40-char SHA above, then re-run."
  exit 1
fi

git -C "${TARGET}" fetch --depth 1 origin "${COMMIT}"
git -C "${TARGET}" checkout --detach "${COMMIT}"
