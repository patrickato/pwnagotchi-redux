#!/usr/bin/env bash
# Hardware-free image checks; never fetch sources, mount disks, or build an image.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for tool in bash python3 shellcheck ld; do
    command -v "$tool" >/dev/null || { echo "Image CI needs $tool; see docs/IMAGE_BUILD.md." >&2; exit 1; }
done
python3 - <<'PY'
import os
import shlex
import shutil

compiler = shlex.split(os.environ.get("CC", "cc"))
if not compiler or shutil.which(compiler[0]) is None:
    raise SystemExit("Image CI needs a host C compiler (CC) for synthetic lifecycle tests; see docs/IMAGE_BUILD.md.")
PY
python3 -m compileall -q image boot redux/core/boot.py
python3 -m pytest -o addopts= -q tests/test_image_build.py tests/test_nexmon_build.py tests/test_boot.py tests/test_release_manifest.py tests/test_release_gpt.py tests/test_boot_payload.py tests/test_root_payload.py tests/test_field_export_country.py tests/test_field_image_qualification.py tests/test_host_preflight.py tests/test_pigen_host_compat.py tests/test_pigen_contract.py tests/test_pigen_release_auth.py tests/test_firstboot_partition.py tests/test_operator_provision.py tests/test_release_audit.py tests/test_live_runtime.py tests/test_capture_ingest.py tests/test_frame_headers.py tests/test_passive_qualification.py tests/test_awus036acm.py
