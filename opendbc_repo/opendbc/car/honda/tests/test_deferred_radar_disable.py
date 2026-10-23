from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, DBC, deferred_radar_disable
from opendbc.sunnypilot.car.tests.runtime_config import make_test_car_config

EXT_DIAG = b'\x02\x10\x03\x00\x00\x00\x00\x00'
DISABLE = b'\x03\x28\x83\x03\x00\x00\x00\x00'
TESTER_PRESENT = b'\x02\x3E\x80\x00\x00\x00\x00\x00'


def interface(alpha_long=True):
  config = make_test_car_config()
  cp = CarInterface.get_params(CAR.HONDA_INSIGHT, gen_empty_fingerprint(), [], alpha_long, False, False, config)
  cp_sp = CarInterface.get_params_sp(cp, CAR.HONDA_INSIGHT, gen_empty_fingerprint(), [], alpha_long, False, False)
  return CarInterface(cp, cp_sp, config)


class Bench:
  """Classic Bosch PT bus (1) as the controller sees it. Until it is disabled, the radar sends ACC_CONTROL every 2
  frames and relays the camera's STEERING_CONTROL, relay open or not (the panda forwards the camera to the radar).
  Our TX comes back as a sent echo (src 129) once the panda is in the Honda safety mode, as blocked (src 193) before."""
  def __init__(self, ci):
    self.ci = ci
    self.packer = CANPacker(DBC[ci.CP.carFingerprint][Bus.pt])
    self.frame = 0
    self.switched = False
    self.radar_alive = True
    self.session = False
    self.last_sends = []

  def step(self):
    frames = [CanData(addr, dat, (129 if self.switched else 193)) for addr, dat, _ in self.last_sends]
    if self.radar_alive:
      if self.frame % 2 == 0:
        frames.append(CanData(*self.packer.make_can_msg("ACC_CONTROL", 1, {})))
      frames.append(CanData(*self.packer.make_can_msg("STEERING_CONTROL", 1, {})))
    now = 1_000_000_000 + self.frame * 10_000_000
    self.ci.update([(now, frames)])
    _, sends = self.ci.apply(structs.CarControl().as_reader(), structs.CarControlSP(), now)
    if self.switched:
      self.session = self.session or EXT_DIAG in diag(sends)
      self.radar_alive = self.radar_alive and not (self.session and DISABLE in diag(sends))
    self.last_sends = sends
    self.frame += 1
    return sends


def diag(sends):
  return [dat for addr, dat, _ in sends if addr == 0x18DAB0F1]


def ours(sends):
  return [addr for addr, _, _ in sends if addr in (0xE4, 0x1DF, 0x1EF, 0x30C, 0x39F)]


def test_only_classic_bosch_radar_under_alpha_long():
  assert deferred_radar_disable(interface().CP)
  assert not deferred_radar_disable(interface(alpha_long=False).CP)


def test_init_leaves_the_radar_alone():
  sent = []
  ci = interface()
  CarInterface.init(ci.CP, ci.CP_SP, lambda *a, **k: [], lambda msgs: sent.append(msgs))
  assert not sent


def test_radar_silenced_once_panda_passes_our_tx_and_handoff_is_gapless():
  bench = Bench(interface())

  # ELM327/noOutput: radar left alone, nothing of ours that would collide with the radar's or camera's streams
  for _ in range(300):
    sends = bench.step()
    assert not diag(sends) and not ours(sends)

  # safety mode switch: session, then disable, well inside the panda's 1 s relay-malfunction grace
  bench.switched = True
  for i in range(30):
    sends = bench.step()
    if not bench.radar_alive:
      break
    assert not ours(sends)
  assert not bench.radar_alive and i <= 20

  # radar quiet: our ACC_CONTROL and STEERING_CONTROL within 6 frames (VSA faults after ~0.1 s), then tester present
  first = {}
  sent_diag = []
  for i in range(30):
    sends = bench.step()
    for addr in ours(sends):
      first.setdefault(addr, i)
    sent_diag += diag(sends)
  assert first[0x1DF] <= 6 and first[0xE4] <= 6
  assert sent_diag and set(sent_diag) == {TESTER_PRESENT}


def test_radar_left_on_if_panda_never_switches():
  bench = Bench(interface())
  for _ in range(1500):
    sends = bench.step()
    assert not diag(sends) and not ours(sends)
  assert bench.radar_alive
