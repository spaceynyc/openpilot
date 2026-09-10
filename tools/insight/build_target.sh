#!/usr/bin/env bash
set -euo pipefail
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_root="${1:?Usage: build_target.sh /absolute/build-root}"
image_name="${INSIGHT_BUILD_IMAGE:-starpilot-insight-builder:2026-09-10}"
[[ "${build_root}" = /* ]] || { echo 'Build root must be absolute'; exit 2; }
[[ -x "${build_root}/venv/bin/python3" ]] || { echo 'Install frozen target dependencies first'; exit 2; }
mkdir -p "${build_root}/cache/scons-target" "${build_root}/logs"
docker run --rm \
  -v "${build_root}:/validation" \
  -v "${build_root}/sysroot/system/vendor/lib64:/system/vendor/lib64:ro" \
  -v "${source_root}:${source_root}:ro" \
  -w /validation/runtime \
  -e GIT_WORK_TREE=/validation/runtime -e PYTHONPATH=/validation/runtime \
  -e SP_FORCE_TICI=1 -e SP_FORCE_ARCH=larch64 -e SP_TICI_SYSROOT=/validation/sysroot \
  -e SP_BUILD_WARP_ARTIFACTS=0 -e SP_SKIP_DM_TINYGRAD_PKL=1 \
  -e SP_SCONS_CACHE_DIR=/validation/cache/scons-target -e UV_CACHE_DIR=/validation/cache/uv \
  "${image_name}" bash -lc '
    source /validation/venv/bin/activate
    export LD_LIBRARY_PATH=/usr/local/lib:/validation/sysroot/usr/local/lib:/validation/sysroot/usr/lib/aarch64-linux-gnu:/validation/sysroot/lib/aarch64-linux-gnu:/system/vendor/lib64
    scons -j4 --cache-disable > /validation/logs/target-build.log 2>&1
    build_result=$?
    tail -45 /validation/logs/target-build.log
    exit "$build_result"
  '
