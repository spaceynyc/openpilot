from pathlib import Path

from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths

from openpilot.starpilot.common.starpilot_utilities import use_konik_server


UNREGISTERED_DONGLE_ID = "UnregisteredDevice"


def _cache_params_path() -> str:
  return Paths.params_cache_root()


def _normalize_dongle_id(value):
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="ignore")
  if value is None:
    return None
  value = str(value).strip()
  return value or None


def _usable_dongle_id(value):
  value = _normalize_dongle_id(value)
  return value if value not in (None, UNREGISTERED_DONGLE_ID) else None


def _read_persisted_stock_dongle_id():
  persisted_dongle_id_path = Path(Paths.persist_root()) / "comma" / "dongle_id"
  if not persisted_dongle_id_path.is_file():
    return None
  return _usable_dongle_id(persisted_dongle_id_path.read_text())


def prepare_konik_server_switch(use_konik, params, params_cache=None):
  if params_cache is None:
    params_cache = Params(_cache_params_path())

  stable_dongle_id = _ensure_stock_dongle_id(params)
  if stable_dongle_id is None:
    stable_dongle_id = _usable_dongle_id(params.get("DongleId"))

  params.put_bool("UseKonikServer", use_konik)
  params_cache.put_bool("UseKonikServer", use_konik)

  # Konik accepts the device's existing ID. Reusing it avoids a second
  # registration and keeps switching endpoints a local, instant operation.
  if stable_dongle_id is not None:
    params.put("DongleId", stable_dongle_id)
    params_cache.put("DongleId", stable_dongle_id)
    params.put("StockDongleId", stable_dongle_id)
    params_cache.put("StockDongleId", stable_dongle_id)
    if use_konik:
      params.put("KonikDongleId", stable_dongle_id)
      params_cache.put("KonikDongleId", stable_dongle_id)


def _ensure_stock_dongle_id(params):
  current_dongle_id = _usable_dongle_id(params.get("DongleId"))
  stock_dongle_id = _usable_dongle_id(params.get("StockDongleId"))

  if stock_dongle_id is not None:
    return stock_dongle_id

  candidate = _read_persisted_stock_dongle_id()
  if candidate is None:
    candidate = current_dongle_id

  if candidate is not None and candidate != stock_dongle_id:
    params.put("StockDongleId", candidate)

  return candidate


def sync_konik_dongle_id(params):
  current_dongle_id = _usable_dongle_id(params.get("DongleId"))
  stock_dongle_id = _ensure_stock_dongle_id(params)
  stable_dongle_id = stock_dongle_id or _read_persisted_stock_dongle_id() or current_dongle_id

  if use_konik_server():
    # Never contact pilotauth from the boot path. NRDR uses the already-issued
    # device ID with Konik; doing the same also avoids Konik's 403 response for
    # serials that are already registered.
    if stable_dongle_id is not None:
      params.put("KonikDongleId", stable_dongle_id)
      if current_dongle_id != stable_dongle_id:
        params.put("DongleId", stable_dongle_id)
  elif stock_dongle_id is not None and current_dongle_id != stock_dongle_id:
    params.put("DongleId", stock_dongle_id)
