#!/usr/bin/env python3
"""Read-only, allowlisted export. Run with the INSTALLED fork's Python environment.

No identity, VIN, keys, routes or private endpoints are emitted. CarParams is a
saved effective snapshot; this export does not claim live controller observation.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path('/data/openpilot')
PARAMS = Path('/data/params/d')
KEYS = [
  'IsOffroad', 'CarParams', 'CarParamsPersistent', 'CarParamsCache',
]
TUNE_KEYS = [
  *[f'Lat{term}Scale{band}' for term in 'PIF' for band in ('LowSpeed', 'Standard', 'Highway')],
  'HondaCenterScale', 'HondaCenterBoostThreshold', 'HondaCenterBoostMinSpeed',
  'NrdrLatRateDamping', 'NrdrLatRateDampingFadeSpeed', 'NrdrLatStiction',
  'HondaTorqueLowPassFilter', 'HondaLpfTauLowSpeed', 'HondaLpfTauStandard', 'HondaLpfTauHighway',
  'HondaSteerDeltaLimiter', 'HondaSteerDeltaUp', 'HondaSteerDeltaDown',
  'NrdrSteerRatioMode', 'NrdrSteerRatioManualCenter', 'NrdrSteerRatioManualFinal',
  'HondaBoschARadar', 'BoschARadar', 'BlotV2', 'ExperimentalLongitudinalEnabled',
  'NrdrInterpolatedTorquePifBlend', 'TorqueLateralControl', 'NeuralFF', 'NNFF',
  'UseKonikServer', 'InsightSteeringEnabled', 'InsightEpsProfile',
]

def text(key):
  try:
    value = (PARAMS / key).read_text().strip()
    return value if len(value) < 100 else '<oversized>'
  except FileNotFoundError:
    return None


def main():
  try:
    from cereal import car
  except ModuleNotFoundError:
    from openpilot.cereal import car
  result = {'schema': 1, 'revision': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
            'branch': subprocess.check_output(['git', '-C', str(ROOT), 'branch', '--show-current'], text=True).strip(),
            'offroad': text('IsOffroad'), 'settings': {k: text(k) for k in TUNE_KEYS}, 'carParams': []}
  for key in KEYS[1:]:
    path = PARAMS / key
    if not path.exists():
      continue
    with car.CarParams.from_bytes(path.read_bytes()) as cp:
      pid = cp.lateralTuning.pid.to_dict() if cp.lateralTuning.which() == 'pid' else None
      result['carParams'].append({
        'source': key, 'savedAt': path.stat().st_mtime, 'fingerprint': str(cp.carFingerprint),
        'controller': cp.lateralTuning.which(), 'pid': pid, 'steerRatio': float(cp.steerRatio),
        'torqueMap': cp.lateralParams.to_dict(), 'openpilotLongitudinalControl': bool(cp.openpilotLongitudinalControl),
        'radarUnavailable': bool(cp.radarUnavailable), 'flags': int(cp.flags),
        'epsVersions': [f.fwVersion.decode('ascii', errors='replace').rstrip('\x00') for f in cp.carFw if f.ecu == 'eps'],
        'safetyConfigs': cp.safetyConfigs.to_list(), 'alternativeExperience': int(cp.alternativeExperience),
      })
  print(json.dumps(result, indent=2, allow_nan=False))

if __name__ == '__main__':
  main()
