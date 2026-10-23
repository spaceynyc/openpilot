from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, DBC, deferred_radar_disable

EXT_DIAG = b'\x02\x10\x03\x00\x00\x00\x00\x00'
DISABLE = b'\x03\x28\x83\x03\x00\x00\x00\x00'
TESTER_PRESENT = b'\x02\x3E\x80\x00\x00\x00\x00\x00'


def interface(alpha_long=True):
  cp = CarInterface.get_params(CAR.HONDA_INSIGHT, gen_empty_fingerprint(), [], alpha_long, False, False)
  return CarInterface(cp)


class Bench:
  """Classic Bosch PT bus as the controller sees it: the live radar sends ACC_CONTROL every 2 frames and, while the
  relay is closed, forwards the camera's STEERING_CONTROL."""
  def __init__(self, ci):
    self.ci = ci
    self.packer = CANPacker(DBC[ci.CP.carFingerprint][Bus.pt])
    self.frame = 0

  def step(self, radar=True, camera=True):
    frames = []
    if radar and self.frame % 2 == 0:
      addr, dat, bus = self.packer.make_can_msg("ACC_CONTROL", 1, {})
      frames.append(CanData(addr, dat, bus))
    if camera:
      addr, dat, bus = self.packer.make_can_msg("STEERING_CONTROL", 1, {})
      frames.append(CanData(addr, dat, bus))
    now = 1_000_000_000 + self.frame * 10_000_000
    self.ci.update([(now, frames)])
    _, sends = self.ci.apply(structs.CarControl().as_reader(), now)
    self.frame += 1
    return sends


def diag(sends):
  return [dat for addr, dat, _ in sends if addr == 0x18DAB0F1]


def acc(sends):
  return [addr for addr, _, _ in sends if addr in (0x1DF, 0x1EF, 0x30C, 0x39F)]


def test_only_classic_bosch_radar_under_alpha_long():
  assert deferred_radar_disable(interface().CP)
  assert not deferred_radar_disable(interface(alpha_long=False).CP)


def test_init_leaves_the_radar_alone():
  sent = []
  CarInterface.init(interface().CP, lambda *a, **k: [], lambda msgs: sent.append(msgs))
  assert not sent


def test_radar_silenced_only_after_relay_opens_and_handoff_is_gapless():
  bench = Bench(interface())

  # relay closed, radar alive: nothing of ours that would collide with the radar
  for _ in range(100):
    sends = bench.step()
    assert not diag(sends) and not acc(sends)

  # relay opens (camera disappears from the PT bus): session, then disable 50 ms later, still no overlap
  sent_diag = []
  for _ in range(10):
    sends = bench.step(camera=False)
    sent_diag += diag(sends)
    assert not acc(sends)
  assert sent_diag == [EXT_DIAG, DISABLE]

  # radar goes quiet: our ACC_CONTROL within 6 frames (VSA faults after ~0.1 s), then tester present
  first_acc = None
  sent_diag = []
  for i in range(30):
    sends = bench.step(radar=False, camera=False)
    if first_acc is None and 0x1DF in acc(sends):
      first_acc = i
    sent_diag += diag(sends)
  assert first_acc is not None and first_acc <= 6
  assert sent_diag and set(sent_diag) == {TESTER_PRESENT}


def test_relay_fallback_when_camera_never_seen():
  bench = Bench(interface())
  sent_diag = []
  for _ in range(510):
    sent_diag += diag(bench.step(camera=False))
  assert sent_diag[:2] == [EXT_DIAG, DISABLE]
