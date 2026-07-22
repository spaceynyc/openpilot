import json
from Crypto.PublicKey import RSA
from pathlib import Path

from openpilot.common.params import Params
from openpilot.system.athena.registration import register, UNREGISTERED_DONGLE_ID
from openpilot.system.athena.tests.helpers import MockResponse
from openpilot.system.hardware.hw import Paths


class TestRegistration:

  def setup_method(self):
    # clear params and setup key paths
    self.params = Params()

    persist_dir = Path(Paths.persist_root()) / "comma"
    persist_dir.mkdir(parents=True, exist_ok=True)

    self.priv_key = persist_dir / "id_rsa"
    self.pub_key = persist_dir / "id_rsa.pub"
    self.dongle_id = persist_dir / "dongle_id"

  def _generate_keys(self):
    self.pub_key.touch()
    k = RSA.generate(2048)
    with open(self.priv_key, "wb") as f:
      f.write(k.export_key())
    with open(self.pub_key, "wb") as f:
      f.write(k.publickey().export_key())

  def test_valid_cache(self, mocker):
    # if all params are written, return the cached dongle id.
    # should work with a dongle ID on either /persist/ or normal params
    self._generate_keys()

    dongle = "DONGLE_ID_123"
    m = mocker.patch("openpilot.system.athena.registration.api_get", autospec=True)
    for persist, params in [(True, True), (True, False), (False, True)]:
      self.params.put("DongleId", dongle if params else "")
      with open(self.dongle_id, "w") as f:
        f.write(dongle if persist else "")
      assert register() == dongle
      assert not m.called

  def test_no_keys(self, mocker):
    # missing pubkey
    m = mocker.patch("openpilot.system.athena.registration.api_get", autospec=True)
    dongle = register()
    assert m.call_count == 0
    assert dongle == UNREGISTERED_DONGLE_ID
    assert self.params.get("DongleId") == dongle

  def test_missing_cache(self, mocker):
    # keys exist but no dongle id
    self._generate_keys()
    m = mocker.patch("openpilot.system.athena.registration.api_get", autospec=True)
    dongle = "DONGLE_ID_123"
    m.return_value = MockResponse(json.dumps({'dongle_id': dongle}), 200)
    assert register() == dongle
    assert m.call_count == 1

    # call again, shouldn't hit the API this time
    assert register() == dongle
    assert m.call_count == 1
    assert self.params.get("DongleId") == dongle

  def test_konik_reuses_existing_id_without_registration(self, mocker):
    self._generate_keys()
    dongle = "DONGLE_ID_123"
    self.params.put("DongleId", dongle)
    self.params.put_bool("UseKonikServer", True)
    m = mocker.patch("openpilot.system.athena.registration.api_get", autospec=True)

    assert register(register_konik=True) == dongle
    assert m.call_count == 0
    assert self.params.get("KonikDongleId") == dongle

  def test_registration_timeout_disables_konik_and_boots_unregistered(self, mocker):
    self._generate_keys()
    self.params.put_bool("UseKonikServer", True)
    m = mocker.patch("openpilot.system.athena.registration.api_get", autospec=True)

    assert register(timeout=0) == UNREGISTERED_DONGLE_ID
    assert m.call_count == 0
    assert not self.params.get_bool("UseKonikServer")
    assert self.params.get_bool("DoReboot")

  def test_registration_retry_obeys_hard_deadline(self, mocker):
    self._generate_keys()
    self.params.put_bool("UseKonikServer", True)
    clock = [0.0]
    mocker.patch("openpilot.system.athena.registration.time.monotonic", side_effect=lambda: clock[0])
    mocker.patch("openpilot.system.athena.registration.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    mocker.patch("openpilot.system.athena.registration.HARDWARE.get_imei", side_effect=lambda slot: f"imei-{slot}")
    m = mocker.patch("openpilot.system.athena.registration.api_get", side_effect=OSError("offline"))

    assert register(timeout=0.5) == UNREGISTERED_DONGLE_ID
    assert m.call_count == 1
    assert m.call_args.kwargs["timeout"] <= 0.25
    assert clock[0] == 0.5
    assert not self.params.get_bool("UseKonikServer")
    assert self.params.get_bool("DoReboot")

  def test_unregistered(self, mocker):
    # keys exist, but unregistered
    self._generate_keys()
    m = mocker.patch("openpilot.system.athena.registration.api_get", autospec=True)
    m.return_value = MockResponse(None, 402)
    dongle = register()
    assert m.call_count == 1
    assert dongle == UNREGISTERED_DONGLE_ID
    assert self.params.get("DongleId") == dongle
