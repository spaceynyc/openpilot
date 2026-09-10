import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from openpilot.starpilot.insight import konik
from openpilot.starpilot.insight import backend
from openpilot.starpilot.common import connect_server

KONIK_ID = "a" * 16
STOCK_ID = "b" * 16
OTHER_ID = "c" * 16


class Params:
  def __init__(self, **data):
    self.data = data

  def get(self, key):
    return self.data.get(key)

  def put(self, key, value):
    self.data[key] = value

  def put_bool(self, key, value):
    self.data[key] = bool(value)

  def get_bool(self, key):
    return bool(self.data.get(key, False))

  def remove(self, key):
    self.data.pop(key, None)


@pytest.fixture(autouse=True)
def isolated_identity(monkeypatch, tmp_path):
  monkeypatch.setenv("KONIK_DONGLE_ID_PATH", str(tmp_path / "identity/konik_dongle_id"))
  monkeypatch.setattr(konik.Paths, "persist_root", lambda: str(tmp_path / "persist"))
  clear_backend_cache = backend.use_konik_server.cache_clear
  clear_backend_cache()
  yield
  clear_backend_cache()


def factory_file():
  p = Path(konik.Paths.persist_root()) / "comma/dongle_id"
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(STOCK_ID)
  return p


@pytest.mark.parametrize("value", [None, "", "UnregisteredDevice", "a b", "../identity", "x" * 17, b"\xff" * 16])
def test_invalid_identity(value):
  assert konik.normalize(value) is None


def test_durable_identity_wins_and_preserves_factory():
  factory = factory_file()
  konik.persist(KONIK_ID)
  params = Params(DongleId=OTHER_ID, KonikDongleId=OTHER_ID)
  assert konik.resolve_local(params) == KONIK_ID
  assert params.get("DongleId") == params.get("KonikDongleId") == KONIK_ID
  assert params.get("StockDongleId") == factory.read_text() == STOCK_ID
  assert os.stat(konik.identity_path()).st_mode & 0o777 == 0o600


def test_params_backup_recovers_durable_file_without_network(monkeypatch):
  monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("network worker launched"))
  params = Params(KonikDongleId=KONIK_ID)
  assert konik.resolve_local(params) == KONIK_ID
  assert konik.read_identity(konik.identity_path()) == KONIK_ID


def test_unknown_current_identity_requires_authenticated_migration():
  factory_file()
  params = Params(DongleId=OTHER_ID)
  assert konik.resolve_local(params) == konik.UNREGISTERED
  assert params.get("InsightKonikMigrationId") == OTHER_ID
  assert params.get("StockDongleId") == STOCK_ID
  assert not konik.identity_path().exists()


def test_outage_keeps_backend_and_migration_candidate(monkeypatch):
  params = Params(UseKonikServer=True, InsightKonikMigrationId=OTHER_ID)
  def unavailable(*args, **kwargs):
    raise subprocess.TimeoutExpired("worker", 15)
  monkeypatch.setattr(subprocess, "run", unavailable)
  assert konik.bounded_registration(params) is None
  assert params.get("UseKonikServer") is True
  assert params.get("InsightKonikMigrationId") == OTHER_ID
  assert not params.get("DoReboot")
  assert not konik.identity_path().exists()


def test_deadline_kills_a_hung_worker(monkeypatch):
  real_run = subprocess.run
  def hung_worker(*args, **kwargs):
    return real_run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
  monkeypatch.setattr(subprocess, "run", hung_worker)
  start = time.monotonic()
  assert konik.bounded_registration(Params(), timeout=0.2) is None
  assert time.monotonic() - start < 2.0


def test_success_persists_and_survives_params_reset(monkeypatch):
  factory_file()
  monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps({"dongle_id": KONIK_ID})))
  assert konik.bounded_registration(Params()) == KONIK_ID
  rebooted = Params()
  assert konik.resolve_local(rebooted) == KONIK_ID
  assert rebooted.get("StockDongleId") == STOCK_ID


@pytest.mark.parametrize("body", ["", "not json", '{"dongle_id": "UnregisteredDevice"}', '[]', '{}'])
def test_bad_registration_response_is_not_committed(monkeypatch, body):
  monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=body))
  assert konik.bounded_registration(Params()) is None
  assert not konik.identity_path().exists()


@pytest.mark.parametrize("selected", [True, False])
def test_switch_preserves_both_identities_until_restart(selected):
  params = Params(DongleId=KONIK_ID, KonikDongleId=KONIK_ID, StockDongleId=STOCK_ID)
  cache = Params(**params.data)
  connect_server.prepare_konik_server_switch(selected, params, cache)
  for store in (params, cache):
    assert store.get("UseKonikServer") is selected
    assert store.get("KonikDongleId") == KONIK_ID
    assert store.get("StockDongleId") == STOCK_ID
    assert store.get("DongleId") == KONIK_ID


@pytest.mark.parametrize("selected, expected", [(True, ("https://api.konik.ai", "wss://athena.konik.ai")),
                                               (False, ("https://api.commadotai.com", "wss://athena.comma.ai"))])
def test_all_endpoints_follow_one_selection(monkeypatch, selected, expected):
  monkeypatch.setattr(backend, "Params", lambda **_: Params(UseKonikServer=selected))
  monkeypatch.setenv("API_HOST", "https://mismatched.invalid")
  monkeypatch.setenv("ATHENA_HOST", "wss://mismatched.invalid")
  assert backend.endpoints() == expected


@pytest.mark.parametrize("status, device, expected", [
  (200, {"dongle_id": OTHER_ID, "serial": "test-serial"}, OTHER_ID),
  (200, {"dongle_id": KONIK_ID, "serial": "test-serial"}, None),
  (200, {"dongle_id": OTHER_ID, "serial": "another-serial"}, None),
  (503, {}, None),
])
def test_candidate_requires_matching_authenticated_record(monkeypatch, status, device, expected):
  from openpilot.common import api
  from openpilot.common import params as native_params
  from openpilot.system.hardware import HARDWARE
  monkeypatch.setattr(backend, "use_konik_server", lambda: True)
  monkeypatch.setattr(native_params, "Params", lambda: Params(InsightKonikMigrationId=OTHER_ID))
  monkeypatch.setattr(HARDWARE, "get_serial", lambda: "test-serial")
  class CandidateApi:
    def __init__(self, dongle_id):
      assert dongle_id == OTHER_ID
    def get_token(self):
      return "test-auth-token"
    def get(self, endpoint, **kwargs):
      assert kwargs["access_token"] == "test-auth-token"
      return SimpleNamespace(status_code=status, json=lambda: device)
  monkeypatch.setattr(api, "Api", CandidateApi)
  monkeypatch.setattr(api, "api_get", lambda *a, **k: pytest.fail("inconclusive identity must not register"))
  assert konik._worker() == expected
