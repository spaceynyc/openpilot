from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.starpilot.insight.backend import use_konik_server
from openpilot.starpilot.insight.konik import resolve_local, stock_identity


def _cache_params_path() -> str:
  return Paths.params_cache_root()


def prepare_konik_server_switch(use_konik, params, params_cache=None):
  if params_cache is None:
    params_cache = Params(_cache_params_path())
  # Endpoint/identity changes are applied together after restart. Preserve both
  # backend identities, including when the selected service is unavailable.
  params.put_bool("UseKonikServer", use_konik)
  params_cache.put_bool("UseKonikServer", use_konik)


def sync_konik_dongle_id(params):
  if use_konik_server():
    resolve_local(params)
  else:
    factory = stock_identity(params)
    if factory is not None:
      params.put("StockDongleId", factory)
      params.put("DongleId", factory)
