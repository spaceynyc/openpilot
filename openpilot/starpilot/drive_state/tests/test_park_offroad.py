import math

from opendbc.can import CANPacker, CANParser
from openpilot.starpilot.drive_state.park_offroad import FRESH_S, PARK_HOLD_S, ParkOffroad

DBC = "honda_insight_ex_2019_can_generated"
P, D = 1, 4


class Bench:
  def __init__(self):
    self.t = 100.0
    self.packer = CANPacker(DBC)
    self.frames = []
    self.watch = ParkOffroad(params=None, clock=lambda: self.t)
    # what _refresh sets up for an Insight, without the CarParams read or the CAN socket
    self.watch.enabled = True
    self.watch._checked_at = math.inf
    self.watch._parser = CANParser(DBC, [("GEARBOX_AUTO", math.nan), ("ENGINE_DATA", math.nan)], 1)
    self.watch._read = self._take

  def _take(self):
    frames, self.frames = self.frames, []
    return frames

  def step(self, gear=None, kph=0.0, dt=0.1):
    self.t += dt
    if gear is not None:
      sent = [self.packer.make_can_msg("GEARBOX_AUTO", 1, {"GEAR_SHIFTER": gear}),
              self.packer.make_can_msg("ENGINE_DATA", 1, {"XMISSION_SPEED": kph})]
      self.frames.append((int(self.t * 1e9), sent))
    return self.watch.update()


def run(bench, seconds, **kw):
  return [bench.step(**kw) for _ in range(round(seconds / 0.1))]


def test_park_counts_after_the_hold_and_drive_releases_at_once():
  bench = Bench()
  held = run(bench, PARK_HOLD_S + 0.5, gear=P)
  assert not any(held[:round(PARK_HOLD_S / 0.1) - 1]) and held[-1]
  assert not bench.step(gear=D)


def test_passing_through_park_while_shifting_does_not_count():
  bench = Bench()
  run(bench, 1.0, gear=P)
  assert not any(run(bench, 0.3, gear=2) + run(bench, PARK_HOLD_S - 0.3, gear=P))


def test_rolling_or_stale_or_disabled_is_never_parked():
  bench = Bench()
  assert not any(run(bench, PARK_HOLD_S + 1, gear=P, kph=3.0))
  run(bench, PARK_HOLD_S + 0.5, gear=P)
  assert bench.step(gear=P)
  stale = run(bench, 1.5)  # frames stop arriving: trusted for FRESH_S, then not parked
  assert not any(stale[round(FRESH_S / 0.1) + 1:])
  bench = Bench()
  bench.watch.enabled = False
  assert not any(run(bench, PARK_HOLD_S + 1, gear=P))


def test_a_broken_reader_falls_back_to_not_parked():
  bench = Bench()
  def boom():
    raise OSError("socket gone")
  bench.watch._read = boom
  assert not bench.step()
