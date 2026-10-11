#!/usr/bin/env bash
# Hardware-free image checks; never fetch sources, mount disks, or build an image.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for tool in bash python3 shellcheck; do
    command -v "$tool" >/dev/null || { echo "Image CI needs $tool; see docs/IMAGE_BUILD.md." >&2; exit 1; }
done
python3 -m compileall -q image boot redux/core/boot.py
python3 -m pytest -o addopts= -q tests/test_image_build.py tests/test_nexmon_build.py tests/test_boot.py tests/test_release_manifest.py tests/test_release_gpt.py tests/test_boot_payload.py tests/test_host_preflight.py tests/test_pigen_contract.py tests/test_release_audit.py tests/test_live_runtime.py tests/test_capture_ingest.py
