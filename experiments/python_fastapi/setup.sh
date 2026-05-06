#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${ROOT}/checkout"
REPO="https://github.com/tiangolo/full-stack-fastapi-template.git"
COMMIT="13652b51ea0acca7dfe243ac25e2bbdc066f3c4f"

if [[ ! -d "${TARGET}/.git" ]]; then
  git clone "${REPO}" "${TARGET}"
fi

git -C "${TARGET}" fetch --depth 1 origin "${COMMIT}"
git -C "${TARGET}" checkout --detach "${COMMIT}"
