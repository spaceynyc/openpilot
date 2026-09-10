import copy
import json

import pytest

from openpilot.common.params import Params
from openpilot.tools.insight.migration import prepare, apply_preview


def exported():
  return {'schema': 1, 'revision': 'd' * 40, 'settings': {
    'LatPScaleLowSpeed': '90', 'LatPScaleStandard': '90', 'LatPScaleHighway': '95',
    'NrdrLatStiction': '0', 'HondaTorqueLowPassFilter': '1',
    'HondaBoschARadar': '1', 'HondaSteerDeltaUp': '4', 'HondaSteerDeltaDown': '4',
  }, 'carParams': [{
    'fingerprint': 'HONDA_INSIGHT', 'controller': 'pid', 'source': 'CarParamsPersistent', 'savedAt': 1,
    'openpilotLongitudinalControl': False, 'epsVersions': ['39990-TXM,A040'],
    'pid': {'kpBP': [0.], 'kpV': [.03], 'kiBP': [0.], 'kiV': [.01], 'kf': .000012},
  }]}


def test_preview_preserves_base_and_multipliers_and_reports_radar_migration():
  data = exported()
  before = copy.deepcopy(data)
  preview = prepare(data, 'ptm')
  assert data == before
  values = preview['values']
  assert values['InsightEpsProfile'] == 3
  assert values['InsightBasePid']['kpV'] == [.03]
  assert values['LatPScaleLowSpeed'] == values['LatPScaleStandard'] == 90
  assert values['LatPScaleHighway'] == 95
  assert not values['NrdrLatStiction']
  assert values['HondaTorqueLowPassFilter']
  assert values['AlphaLongitudinalEnabled']
  assert preview['savedNrdrRadarPreference'] and not values['BoschARadar']
  assert not values['BlotV2']
  assert len(preview['adjustments']) == 2


def test_apply_uses_real_native_params_and_protected_rollback(tmp_path):
  params = Params(str(tmp_path / 'params'))
  params.put_bool('IsOffroad', True)
  params.put('LatPScaleLowSpeed', 100)
  backup = tmp_path / 'rollback.json'
  preview = prepare(exported(), 'ptm')
  apply_preview(preview, params, backup)
  assert backup.stat().st_mode & 0o777 == 0o600
  assert json.loads(backup.read_text())['values']['LatPScaleLowSpeed'] == 100
  assert params.get('InsightSteeringEnabled') is True
  assert params.get('InsightBasePid')['kpV'] == [.03]
  assert params.get('BoschARadar') is False
  with pytest.raises(FileExistsError):
    apply_preview(preview, params, backup)


def test_migration_rejects_onroad_and_unknown_settings_before_writes(tmp_path):
  params = Params(str(tmp_path / 'params'))
  preview = prepare(exported(), 'ptm')
  backup = tmp_path / 'rollback.json'
  with pytest.raises(ValueError, match='offroad'):
    apply_preview(preview, params, backup)
  assert not backup.exists()
  params.put_bool('IsOffroad', True)
  preview['values']['UnknownKey'] = True
  with pytest.raises(ValueError, match='Unapproved'):
    apply_preview(preview, params, backup)
  assert not backup.exists()
  assert params.get('InsightSteeringEnabled') is None
