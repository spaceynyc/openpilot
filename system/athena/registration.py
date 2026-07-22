#!/usr/bin/env python3
import time
import json
import jwt
from typing import cast
from pathlib import Path

from datetime import datetime, timedelta, UTC
from openpilot.common.api import api_get, get_key_pair
from openpilot.common.params import Params
from openpilot.common.spinner import Spinner
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.system.hardware import HARDWARE, PC
from openpilot.system.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog


UNREGISTERED_DONGLE_ID = "UnregisteredDevice"
REGISTRATION_TIMEOUT_S = 60.0
REGISTRATION_REQUEST_TIMEOUT_S = 15.0


def _normalize_dongle_id(value) -> str | None:
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="ignore")
  if value is None:
    return None
  value = str(value).strip()
  return value or None


def _usable_dongle_id(value) -> str | None:
  value = _normalize_dongle_id(value)
  return value if value not in (None, UNREGISTERED_DONGLE_ID) else None


def _read_persisted_dongle_id() -> str | None:
  dongle_path = Path(Paths.persist_root()) / "comma" / "dongle_id"
  try:
    return _usable_dongle_id(dongle_path.read_text()) if dongle_path.is_file() else None
  except OSError:
    cloudlog.exception("failed to read persisted dongle_id")
    return None


def _existing_dongle_id(params: Params) -> str | None:
  # Prefer the original/persisted identity when migrating from the old
  # two-ID Konik implementation, then fall back to the live value.
  return (_usable_dongle_id(params.get("StockDongleId")) or
          _read_persisted_dongle_id() or
          _usable_dongle_id(params.get("DongleId")))


def _mirror_dongle_id_to_persist(dongle_id: str) -> None:
  if _usable_dongle_id(dongle_id) is None:
    return

  try:
    dongle_path = Path(Paths.persist_root()) / "comma" / "dongle_id"
    if not dongle_path.is_file():
      dongle_path.parent.mkdir(parents=True, exist_ok=True)
      dongle_path.write_text(dongle_id)
  except OSError:
    cloudlog.exception("failed to mirror dongle_id to persist")


def _disable_konik_after_registration_failure(params: Params) -> None:
  if not params.get_bool("UseKonikServer"):
    return

  cloudlog.warning("Konik registration failed; falling back to comma connect on the next boot")
  params.put_bool("UseKonikServer", False)
  params.put_bool("DoReboot", True)
  try:
    Params(Paths.params_cache_root()).put_bool("UseKonikServer", False)
  except Exception:
    cloudlog.exception("failed to update cached Konik setting")

  marker = (Path(Paths.comma_home()) / "starpilot" / "cache" / "use_konik") if PC else Path("/cache/use_konik")
  try:
    marker.unlink(missing_ok=True)
  except OSError:
    cloudlog.exception("failed to remove Konik reboot marker")


def is_registered_device() -> bool:
  dongle = Params().get("DongleId")
  return dongle not in (None, UNREGISTERED_DONGLE_ID)


def register(show_spinner=False, register_konik=False, timeout=REGISTRATION_TIMEOUT_S) -> str | None:
  """
  All devices built since March 2024 come with all
  info stored in /persist/. This is kept around
  only for devices built before then.

  With a backend update to take serial number instead
  of dongle ID to some endpoints, this can be removed
  entirely.
  """
  params = Params()
  existing_dongle_id = _existing_dongle_id(params)
  dongle_id: str | None = existing_dongle_id

  # Kept for compatibility with older StarPilot call sites. Konik must reuse a
  # valid existing identity instead of forcing another pilotauth request.
  if register_konik and existing_dongle_id is not None:
    cloudlog.info("reusing existing dongle_id for Konik")

  # Create registration token, in the future, this key will make JWTs directly
  jwt_algo, private_key, public_key = get_key_pair()

  if not public_key and dongle_id is None:
    dongle_id = UNREGISTERED_DONGLE_ID
    cloudlog.warning("missing public key")
  elif dongle_id is None:
    deadline = time.monotonic() + max(float(timeout), 0.0)
    spinner = None
    if show_spinner:
      spinner = Spinner()
      spinner.update("registering device")

    try:
      serial = HARDWARE.get_serial()
      imei1: str | None = None
      imei2: str | None = None

      while imei1 is None and imei2 is None and time.monotonic() < deadline:
        try:
          imei1, imei2 = HARDWARE.get_imei(0), HARDWARE.get_imei(1)
        except Exception:
          cloudlog.exception("Error getting imei, trying again...")
          remaining = max(deadline - time.monotonic(), 0.0)
          time.sleep(min(1.0, remaining))

      if imei1 is None and imei2 is None:
        cloudlog.error("registration timed out waiting for IMEI")
      else:
        backoff = 0
        while time.monotonic() < deadline:
          try:
            register_token = jwt.encode({'register': True, 'exp': datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)},
                                        cast(str, private_key), algorithm=jwt_algo)
            remaining = max(deadline - time.monotonic(), 0.0)
            # requests applies a scalar timeout to both connection and read
            # phases. Give each at most half the remaining wall-clock budget.
            request_timeout = max(min(REGISTRATION_REQUEST_TIMEOUT_S, remaining / 2), 0.1)
            cloudlog.info("getting pilotauth")
            resp = api_get("v2/pilotauth/", method='POST', timeout=request_timeout,
                           imei=imei1, imei2=imei2, serial=serial, public_key=public_key, register_token=register_token)

            if resp.status_code in (402, 403):
              cloudlog.warning(f"Unable to register device, got {resp.status_code}")
              break

            dongleauth = json.loads(resp.text)
            dongle_id = _usable_dongle_id(dongleauth.get("dongle_id"))
            if dongle_id is not None:
              break
            cloudlog.warning("pilotauth returned an invalid dongle_id")
          except Exception:
            cloudlog.exception("failed to authenticate")

          backoff = min(backoff + 1, 15)
          remaining = max(deadline - time.monotonic(), 0.0)
          time.sleep(min(float(backoff), remaining))

      if dongle_id is None:
        dongle_id = existing_dongle_id or UNREGISTERED_DONGLE_ID
        _disable_konik_after_registration_failure(params)
    finally:
      if spinner is not None:
        spinner.close()

  if dongle_id is not None:
    params.put("DongleId", dongle_id)
    if _usable_dongle_id(dongle_id) is not None:
      params.put("StockDongleId", dongle_id)
      if params.get_bool("UseKonikServer"):
        params.put("KonikDongleId", dongle_id)
      _mirror_dongle_id_to_persist(dongle_id)
    set_offroad_alert("Offroad_UnregisteredHardware", (dongle_id == UNREGISTERED_DONGLE_ID) and not PC)
  return dongle_id


if __name__ == "__main__":
  print(register())
