"""Durable Konik identity, adapted from NRDR d2b2df0 registration.py.

Boot and server switching only use local identity. Migration/first registration
run in a disposable subprocess; its deadline also bounds DNS, hardware calls,
TLS and slow response bodies. Device keys never leave their existing location.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog

UNREGISTERED = "UnregisteredDevice"
REGISTRATION_TIMEOUT_S = 15.0


def normalize(value) -> str | None:
  if isinstance(value, bytes):
    try:
      value = value.decode("ascii")
    except UnicodeError:
      return None
  # Both backends currently issue 16-character identifiers. Do not accept a
  # sentinel, a URL, or arbitrary response text as an authenticated identity.
  return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9]{16}", value) else None


def identity_path() -> Path:
  override = os.environ.get("KONIK_DONGLE_ID_PATH")
  if override:
    return Path(override)
  return (Path(Paths.comma_home()) if PC else Path("/data")) / "community/identity/konik_dongle_id"


def read_identity(path: Path) -> str | None:
  try:
    return normalize(path.read_text(encoding="ascii").strip())
  except (OSError, UnicodeError):
    return None


def stock_identity(params) -> str | None:
  # The factory file belongs to Comma. Never overwrite it with a Konik ID.
  return read_identity(Path(Paths.persist_root()) / "comma/dongle_id") or normalize(params.get("StockDongleId"))


def persist(dongle_id: str) -> None:
  if normalize(dongle_id) is None:
    raise ValueError("Invalid Konik identity")
  path = identity_path()
  path.parent.mkdir(parents=True, exist_ok=True)
  temp_path = None
  try:
    with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=path.parent, delete=False) as f:
      temp_path = Path(f.name)
      os.chmod(f.fileno(), 0o600)
      f.write(dongle_id)
      f.flush()
      os.fsync(f.fileno())
    os.replace(temp_path, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
      os.fsync(directory_fd)
    finally:
      os.close(directory_fd)
  finally:
    if temp_path is not None:
      temp_path.unlink(missing_ok=True)


def local_identity(params) -> str | None:
  return read_identity(identity_path()) or normalize(params.get("KonikDongleId"))


def commit_identity(params, dongle_id: str) -> str:
  factory = stock_identity(params)
  if factory is not None:
    params.put("StockDongleId", factory)
  try:
    persist(dongle_id)
  except OSError:
    cloudlog.warning("Unable to persist Konik identity; retaining Params backup")
  params.put("KonikDongleId", dongle_id)
  params.put("DongleId", dongle_id)
  # This says only where identity came from, not that uploads/heartbeat work.
  params.put("InsightKonikStatus", "identity-ready")
  return dongle_id


def resolve_local(params) -> str:
  dongle_id = local_identity(params)
  if dongle_id is not None:
    return commit_identity(params, dongle_id)
  candidate = normalize(params.get("DongleId"))
  if candidate is not None:
    params.put("InsightKonikMigrationId", candidate)
  factory = stock_identity(params)
  if factory is not None:
    params.put("StockDongleId", factory)
  params.put("DongleId", UNREGISTERED)
  params.put("InsightKonikStatus", "pending-offroad-registration")
  return UNREGISTERED


def bounded_registration(params, timeout: float = REGISTRATION_TIMEOUT_S) -> str | None:
  existing = local_identity(params)
  if existing is not None:
    return commit_identity(params, existing)
  try:
    result = subprocess.run(
      [sys.executable, "-m", "openpilot.starpilot.insight.konik", "--worker"],
      capture_output=True, text=True, timeout=timeout, check=True,
    )
    payload = json.loads(result.stdout)
    dongle_id = normalize(payload.get("dongle_id"))
    if dongle_id is not None:
      params.remove("InsightKonikMigrationId")
      return commit_identity(params, dongle_id)
  except (subprocess.SubprocessError, OSError, ValueError, AttributeError):
    # Never log worker output: backend error bodies may contain private data.
    cloudlog.warning("Konik registration unavailable; retaining selected backend")
  params.put("InsightKonikStatus", "registration-unavailable-retrying-offroad")
  return None


def _worker() -> str | None:
  from datetime import datetime, timedelta, UTC
  import jwt
  from openpilot.common.api import Api, api_get, get_key_pair
  from openpilot.common.params import Params
  from openpilot.system.hardware import HARDWARE
  from openpilot.starpilot.insight.backend import use_konik_server

  if not use_konik_server():
    return None
  params = Params()
  serial = HARDWARE.get_serial()
  candidates = dict.fromkeys(filter(None, [normalize(params.get("InsightKonikMigrationId")), stock_identity(params)]))
  # An unavailable validation is inconclusive. Do not create a second identity
  # while the server cannot confirm or reject the existing one.
  for candidate in candidates:
    api = Api(candidate)
    response = api.get(f"v1.1/devices/{candidate}", timeout=3, access_token=api.get_token())
    if response.status_code == 200:
      device = response.json()
      if device.get("dongle_id") == candidate and device.get("serial") in (None, serial):
        return candidate
      return None
    if response.status_code not in (401, 403, 404):
      return None

  algorithm, private_key, public_key = get_key_pair()
  if not public_key or not private_key:
    return None
  imei1, imei2 = HARDWARE.get_imei(0), HARDWARE.get_imei(1)
  if imei1 is None and imei2 is None:
    return None
  token = jwt.encode({"register": True, "exp": datetime.now(UTC) + timedelta(hours=1)}, private_key, algorithm=algorithm)
  response = api_get("v2/pilotauth/", method="POST", timeout=3, imei=imei1, imei2=imei2,
                     serial=serial, public_key=public_key, register_token=token)
  response.raise_for_status()
  return normalize(response.json().get("dongle_id"))


if __name__ == "__main__":
  if sys.argv[1:] != ["--worker"]:
    raise SystemExit("Use the offroad registration service")
  try:
    print(json.dumps({"dongle_id": _worker()}))
  except Exception:
    # The parent receives a failure code without printing tokens or identities.
    raise SystemExit(1) from None
