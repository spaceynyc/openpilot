"""EV9 pending startup retains source UDS outcomes and waits for replacement controls."""
import time

from opendbc.car.hyundai.ev9_longitudinal import qualified
from opendbc.car.hyundai.ioniq5pe_startup import PEPendingStartup
from opendbc.car.isotp_parallel_query import IsoTpParallelQuery


class EV9PendingStartup(PEPendingStartup):
  @staticmethod
  def profile_qualified(cp):
    return qualified(cp)

  def __init__(self, cp, callbacks, *, stock_cp, exchange=None, **kwargs):
    self.disable_response = None
    super().__init__(cp, callbacks, stock_cp=stock_cp,
                     exchange=self._ev9_exchange if exchange is None else exchange, **kwargs)

  def _ev9_exchange(self, recv, send, request, response):
    attempts = 10 if request == b"\x10\x03" else 1
    for _ in range(attempts):
      query = IsoTpParallelQuery(send, recv, 1, [0x730], [request], [b""])
      replies = query.get_data(.1, total_timeout=.1)
      if request == b"\x28\x01\x01":
        if not replies:
          # Silence is sent, unconfirmed; the inherited post-request quiet and
          # independent-source proof must still pass before ownership commits.
          self.disable_response = 'no_reply_sent_unconfirmed'
          return True
        valid = all(payload == b"\x68\x01" for payload in replies.values())
        self.disable_response = 'positive_communication_control' if valid else 'negative_or_malformed_reply'
        return valid
      payload = replies.get((0x730, None))
      if request in (b"\x10\x03", b"\x10\x01"):
        valid = payload is not None and payload.startswith(response) and len(payload) in (2, 6)
      else:
        valid = payload == response
      if valid:
        if request == b"\x10\x03":
          time.sleep(.05)
        return True
      if request == b"\x10\x03":
        time.sleep(.1)
    return False
