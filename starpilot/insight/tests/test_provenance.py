import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_candidate_sources_and_native_import_paths():
  manifest = ROOT / '.insight-source-manifest.json'
  if not manifest.exists() and not os.getenv('INSIGHT_REQUIRE_PROVENANCE'):
    pytest.skip('Run tools/insight/sync_validation.py to create the isolated validation tree')
  source_hashes = json.loads(manifest.read_text())
  for name, digest in source_hashes.items():
    if digest is not None:
      assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
  modules = [
    'openpilot.starpilot.insight.latcontrol_pid', 'openpilot.starpilot.insight.konik',
    'openpilot.selfdrive.controls.lib.longitudinal_planner', 'openpilot.selfdrive.controls.radard',
    'opendbc.car.honda.radar_interface', 'openpilot.common.params_pyx',
    'openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.c_generated_code.acados_ocp_solver_pyx',
    'msgq.ipc_pyx',
  ]
  for name in modules:
    path = Path(importlib.import_module(name).__file__).resolve()
    assert path.is_relative_to(ROOT), (name, str(path))
    assert '.host_runtime' not in str(path)
