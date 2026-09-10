#!/usr/bin/env python3
"""Prepare a reviewable migration; apply/restore are explicit, offroad-only modes.

Export JSON is allowlisted by export_device.py. A preview does not touch Params.
Keep the preview and backup local; neither contains backend identity or keys.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from openpilot.starpilot.insight.profiles import _validated_pid
from openpilot.starpilot.insight.settings import SETTINGS, bounded

PROFILE_IDS = {'stock': 1, 'legacy': 2, 'ptm': 3}
EXTRA_KEYS = {'InsightSteeringEnabled', 'InsightEpsProfile', 'InsightBasePid',
              'BlotV2', 'BoschARadar', 'AlphaLongitudinalEnabled', 'UseKonikServer'}


def prepare(export, eps_profile):
  if export.get('schema') != 1 or eps_profile not in PROFILE_IDS:
    raise ValueError('Unsupported export or missing explicit EPS profile')
  snapshots = [cp for cp in export.get('carParams', []) if cp.get('fingerprint') == 'HONDA_INSIGHT' and cp.get('controller') == 'pid']
  if not snapshots:
    raise ValueError('An exported effective Insight PID snapshot is required')
  cp = snapshots[0]
  versions = {s.replace(',', '-').rstrip('\x00') for s in cp.get('epsVersions', [])}
  if versions != {'39990-TXM-A040'}:
    raise ValueError('Unrecognized EPS software identifier')
  settings = export.get('settings', {})
  values = {}
  adjustments = []
  for key, spec in SETTINGS.items():
    raw = settings.get(key)
    values[key] = bounded(raw, spec) if raw is not None else spec.default
    if raw is not None and spec.kind != 'bool' and float(raw) != values[key]:
      adjustments.append({'key': key, 'saved': float(raw), 'candidate': values[key]})
  # New experiments remain independently OFF until vehicle validation. The saved
  # NRDR preference is reported explicitly; it is not treated as validation.
  values.update(InsightSteeringEnabled=True, InsightEpsProfile=PROFILE_IDS[eps_profile],
                InsightBasePid=_validated_pid(cp['pid']), BlotV2=False, BoschARadar=False,
                AlphaLongitudinalEnabled=True, UseKonikServer=True)
  return {
    'schema': 1, 'sourceRevision': export['revision'], 'confirmedEpsProfile': eps_profile,
    'savedCarParamsSource': cp['source'], 'savedCarParamsTime': cp['savedAt'],
    'savedOpenpilotLongitudinal': cp['openpilotLongitudinalControl'],
    'savedNrdrRadarPreference': settings.get('HondaBoschARadar') == '1',
    'radarMigrationDecision': 'BoschARadar remains OFF pending real-track validation',
    'adjustments': adjustments, 'values': values,
  }


def validate_values(values):
  if not isinstance(values, dict) or set(values) - (set(SETTINGS) | EXTRA_KEYS):
    raise ValueError('Unapproved settings in migration')
  for key, value in values.items():
    if key in SETTINGS and value != bounded(value, SETTINGS[key]):
      raise ValueError(f'Out-of-range setting: {key}')
    if key == 'InsightBasePid': _validated_pid(value)
    if key == 'InsightEpsProfile' and value not in (0, 1, 2, 3): raise ValueError('Unknown EPS profile')
    if key in EXTRA_KEYS - {'InsightBasePid', 'InsightEpsProfile'} and not isinstance(value, bool):
      raise ValueError(f'Expected boolean: {key}')


def write_private(path, data):
  # Exclusive creation prevents overwriting the only rollback record.
  fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  with os.fdopen(fd, 'w') as file:
    json.dump(data, file, indent=2, allow_nan=False)
    file.write('\n')
    file.flush()
    os.fsync(file.fileno())


def apply_preview(preview, params, backup_path):
  if not params.get_bool('IsOffroad'):
    raise ValueError('Settings migration requires offroad state')
  values = preview['values']
  validate_values(values)
  # A prepared preview never directly enables the unvalidated radar/BLoTv2 ports.
  if values.get('BlotV2') or values.get('BoschARadar'):
    raise ValueError('Validate the features independently before enabling them in settings')
  backup = {'schema': 1, 'values': {key: params.get(key) for key in values}}
  write_private(backup_path, backup)
  # Disable first and enable last: an interrupted group cannot boot a partial tune.
  params.put_bool('InsightSteeringEnabled', False)
  for key, value in values.items():
    if key != 'InsightSteeringEnabled': params.put(key, value)
  params.put_bool('InsightSteeringEnabled', values['InsightSteeringEnabled'])
  for key, value in values.items():
    if params.get(key) != value:
      params.put_bool('InsightSteeringEnabled', False)
      raise RuntimeError(f'Persistence check failed for {key}; custom steering disabled')


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  modes = parser.add_subparsers(dest='mode', required=True)
  preview = modes.add_parser('preview')
  preview.add_argument('export', type=Path)
  preview.add_argument('--eps-profile', choices=PROFILE_IDS, required=True)
  preview.add_argument('--output', type=Path, required=True)
  apply = modes.add_parser('apply')
  apply.add_argument('preview', type=Path)
  apply.add_argument('--backup', type=Path, required=True)
  restore = modes.add_parser('restore')
  restore.add_argument('backup', type=Path)
  args = parser.parse_args()
  if args.mode == 'preview':
    raw = args.export.read_bytes()
    data = prepare(json.loads(raw), args.eps_profile)
    data['exportSha256'] = hashlib.sha256(raw).hexdigest()
    write_private(args.output, data)
    print(f'Preview created: {args.output}; no device settings changed')
    return
  from openpilot.common.params import Params
  params = Params()
  if args.mode == 'apply':
    apply_preview(json.loads(args.preview.read_text()), params, args.backup)
  else:
    if not params.get_bool('IsOffroad'): raise ValueError('Restore requires offroad state')
    values = json.loads(args.backup.read_text())['values']
    if set(values) - (set(SETTINGS) | EXTRA_KEYS): raise ValueError('Unapproved restore keys')
    validate_values({k: v for k, v in values.items() if v is not None})
    params.put_bool('InsightSteeringEnabled', False)
    for key, value in values.items():
      if key == 'InsightSteeringEnabled': continue
      params.remove(key) if value is None else params.put(key, value)
    enabled = values.get('InsightSteeringEnabled')
    params.remove('InsightSteeringEnabled') if enabled is None else params.put_bool('InsightSteeringEnabled', enabled)
    if any(params.get(k) != v for k, v in values.items()): raise RuntimeError('Restore persistence check failed')
  print('Settings persisted and verified; take effect next ignition/restart')

if __name__ == '__main__':
  main()
