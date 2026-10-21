#!/usr/bin/env bash
# Package clean release zip for AURA STUDIO
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python scripts/package_release.py "$@"
