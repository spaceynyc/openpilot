"""One backend choice for registration, API, Athena and upload URL requests.

The existing server toggle takes effect after restart. Do not independently
override API_HOST/ATHENA_HOST: mixing their identities can misroute uploads.
"""
from functools import cache

from openpilot.common.params import Params


@cache
def use_konik_server() -> bool:
  return Params().get_bool("UseKonikServer")


def endpoints() -> tuple[str, str]:
  if use_konik_server():
    return "https://api.konik.ai", "wss://athena.konik.ai"
  return "https://api.commadotai.com", "wss://athena.comma.ai"
