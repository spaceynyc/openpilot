"""Route-visible effective steering metadata; deliberately excludes identity data."""
import json

from openpilot.common.git import get_commit
from openpilot.starpilot.insight.settings import decode, digest, PROFILE_LABELS


def steering_report(CP, controller_name):
  decoded = decode(CP)
  if decoded is None:
    return ''
  profile, settings = decoded
  return json.dumps({
    'schema': 1, 'fork': 'starpilot-insight-next', 'commit': get_commit() or 'unknown',
    'controller': controller_name, 'epsProfile': PROFILE_LABELS[profile],
    'epsVersion': sorted({f.fwVersion.decode('ascii', errors='replace').rstrip('\x00')
                          for f in CP.carFw if f.ecu == 'eps' and f.fwVersion.startswith(b'39990-')}),
    'geometryMode': settings['NrdrSteerRatioMode'], 'settingsDigest': digest(CP),
    'basePid': CP.lateralTuning.pid.to_dict(),
    'pScale': [settings[f'LatPScale{band}'] for band in ('LowSpeed', 'Standard', 'Highway')],
    'iScale': [settings[f'LatIScale{band}'] for band in ('LowSpeed', 'Standard', 'Highway')],
    'fScale': [settings[f'LatFScale{band}'] for band in ('LowSpeed', 'Standard', 'Highway')],
    'stiction': settings['NrdrLatStiction'], 'smoothing': settings['HondaTorqueLowPassFilter'],
  }, sort_keys=True, separators=(',', ':'), allow_nan=False)
