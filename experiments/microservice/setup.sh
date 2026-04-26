#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${ROOT}/nestjs-boilerplate"
REPO="https://github.com/brocoders/nestjs-boilerplate.git"
COMMIT="dd0034750fc7f6ec15712afbecf50fa9828018a2"

if [[ ! -d "${TARGET}/.git" ]]; then
  git clone "${REPO}" "${TARGET}"
fi

git -C "${TARGET}" fetch --depth 1 origin "${COMMIT}"
git -C "${TARGET}" checkout --detach "${COMMIT}"
