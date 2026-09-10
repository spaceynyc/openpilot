import pytest

from test_navigation_params import _params_client, the_galaxy


@pytest.mark.parametrize('key,value,kind', [
  ('InsightEpsProfile', 3, int), ('NrdrSteerRatioManualCenter', 16.82, float),
  ('LatPScaleLowSpeed', 90, int), ('BoschARadar', True, bool), ('BlotV2', True, bool),
])
def test_insight_keys_can_be_saved_and_read_through_galaxy(monkeypatch, key, value, kind):
  client, params = _params_client(monkeypatch, {'IsOffroad': True}, 'mici')
  monkeypatch.setattr(the_galaxy, '_get_param_type_info', lambda: ({key}, {key: kind}))
  monkeypatch.setattr(the_galaxy, 'update_starpilot_toggles', lambda: None)
  response = client.put('/api/params', json={'key': key, 'value': value})
  assert response.status_code == 200
  assert params.values[key] == ('1' if kind is bool else str(value))
  response = client.get('/api/params', query_string={'key': key})
  assert response.status_code == 200
  assert response.text == params.values[key]


@pytest.mark.parametrize('key,value', [
  ('InsightEpsProfile', 7), ('NrdrSteerRatioMode', 2), ('NrdrSteerRatioManualCenter', 99),
  ('LatPScaleLowSpeed', -1), ('LatPScaleLowSpeed', 90.5), ('HondaLpfTauLowSpeed', 'nan'),
])
def test_insight_invalid_edits_are_rejected_before_writing(monkeypatch, key, value):
  client, params = _params_client(monkeypatch, {'IsOffroad': True}, 'mici')
  monkeypatch.setattr(the_galaxy, '_get_param_type_info', lambda: ({key}, {key: float}))
  response = client.put('/api/params', json={'key': key, 'value': value})
  assert response.status_code == 400
  assert key not in params.values
