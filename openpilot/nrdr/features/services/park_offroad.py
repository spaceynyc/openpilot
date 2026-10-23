"""Automatic Offroad while parked, for the 2019 Insight.

hardwared asks this each loop whether the car is parked: in Park, at rest, for PARK_HOLD_S on fresh PT-bus frames.
Card is not running offroad, so the gear is read straight from the CAN socket, the way pandad publishes it in both
states. Anything stale, unreadable or unexpected answers "not parked", which leaves the ordinary ignition behavior.
"""

import math
import time

PARK_HOLD_S = 2.0
FRESH_S = 1.0
AT_REST_KPH = 0.5
CP_RECHECK_S = 30.0
INSIGHT = "HONDA_INSIGHT"


def _insight(params) -> bool:
  from opendbc.car.structs import car
  cp_bytes = params.get("CarParamsPersistent")
  if cp_bytes is None:
    return False
  with car.CarParams.from_bytes(cp_bytes) as CP:
    # with openpilot longitudinal the radar is silenced, and dropping offroad without handing it back latches VSA
    # faults (Brake System, Cruise Fault) until the next ignition cycle, so only stock ACC goes offroad in Park
    return CP.carFingerprint == INSIGHT and not CP.openpilotLongitudinalControl


class ParkOffroad:
  def __init__(self, params, clock=time.monotonic):
    self.params = params
    self.clock = clock
    self.enabled = False
    self._checked_at = -math.inf
    self._read = None  # returns new PT-bus frames in CANParser form
    self._parser = None
    self._seen_at = -math.inf
    self._park_since: float | None = None

  def _refresh(self, now: float) -> None:
    if now - self._checked_at < CP_RECHECK_S:
      return
    self._checked_at = now
    try:
      enabled = _insight(self.params)
    except Exception:
      enabled = False
    if enabled and self._parser is None:
      import openpilot.cereal.messaging as messaging
      from opendbc.can import CANParser
      from openpilot.selfdrive.pandad import can_capnp_to_list
      self._parser = CANParser("honda_insight_ex_2019_can_generated",
                               [("GEARBOX_AUTO", math.nan), ("ENGINE_DATA", math.nan)], 1)
      sock = messaging.sub_sock("can", conflate=False, timeout=0)
      self._read = lambda: can_capnp_to_list(messaging.drain_sock_raw(sock))
    self.enabled = enabled

  def update(self) -> bool:
    """Drain the CAN socket and return whether the car is parked."""
    now = self.clock()
    try:
      self._refresh(now)
      if not self.enabled:
        self._park_since = None
        return False
      frames = self._read()
      if frames and 0x1A3 in self._parser.update(frames):
        self._seen_at = now
      gear = self._parser.vl["GEARBOX_AUTO"]["GEAR_SHIFTER"]
      at_rest = self._parser.vl["ENGINE_DATA"]["XMISSION_SPEED"] < AT_REST_KPH
      if now - self._seen_at > FRESH_S or gear != 1 or not at_rest:  # GEAR_SHIFTER 1 = "P"
        self._park_since = None
        return False
      if self._park_since is None:
        self._park_since = now
      return now - self._park_since >= PARK_HOLD_S
    except Exception:
      self._park_since = None
      return False
