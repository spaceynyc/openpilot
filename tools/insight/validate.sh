#!/usr/bin/env bash
set -euo pipefail
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_root="${1:?Usage: validate.sh /absolute/build-root}"
image_name="${INSIGHT_BUILD_IMAGE:-starpilot-insight-builder:2026-09-10}"
[[ "${build_root}" = /* ]] || exit 2
docker run --rm \
  -v "${build_root}:/validation" \
  -v "${build_root}/sysroot/system/vendor/lib64:/system/vendor/lib64:ro" \
  -v "${source_root}:${source_root}:ro" \
  -w /validation/runtime \
  -e GIT_WORK_TREE=/validation/runtime -e PYTHONPATH=/validation/runtime \
  -e INSIGHT_REQUIRE_PROVENANCE=1 -e SP_DISABLE_HOST_PYTEST_REDIRECT=1 \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "${image_name}" bash -lc '
    set -euo pipefail
    source /validation/venv/bin/activate
    export LD_LIBRARY_PATH=/usr/local/lib:/validation/sysroot/usr/local/lib:/validation/sysroot/usr/lib/aarch64-linux-gnu:/validation/sysroot/lib/aarch64-linux-gnu:/system/vendor/lib64
    python3 -m pytest -c /dev/null --confcutdir=starpilot/insight/tests \
      starpilot/insight/tests selfdrive/controls/lib/tests/test_blotv2.py \
      selfdrive/controls/tests/test_longitudinal_planner.py \
      selfdrive/controls/tests/test_longcontrol.py selfdrive/controls/tests/test_latcontrol.py \
      selfdrive/controls/tests/test_radard_bosch.py \
      opendbc_repo/opendbc/car/honda/tests/test_bosch_a_radar.py \
      -q --junitxml=/validation/logs/target-controls.xml > /validation/logs/target-controls.log 2>&1
    tail -5 /validation/logs/target-controls.log
    # Galaxy import stubs are deliberately confined to a separate interpreter.
    python3 -m pytest -c /dev/null --confcutdir=starpilot/system/the_galaxy/tests \
      starpilot/system/the_galaxy/tests/test_device_settings_layout.py \
      starpilot/system/the_galaxy/tests/test_insight_settings.py \
      -q --junitxml=/validation/logs/target-settings.xml > /validation/logs/target-settings.log 2>&1
    tail -5 /validation/logs/target-settings.log
  '
