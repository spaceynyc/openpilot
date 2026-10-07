"""Crawl/standstill steering calm for Honda modified EPS; nothing may change at road speed."""
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.nrdr.features.lateral import latcontrol_pid
from openpilot.nrdr.features.lateral.crawl_steering import (
  CRAWL_AUTHORITY_BP,
  CRAWL_TARGET_TAU_BP,
  CrawlTarget,
  crawl_authority,
)
from openpilot.nrdr.tests.test_live_pid_updates import make_live_pid, make_snapshot


DT = 0.01
FULL_SPEED = CRAWL_TARGET_TAU_BP[-1]


def jittery_targets(seconds: float = 4.0) -> np.ndarray:
  # The logged pattern: the target jumps several degrees every model frame or two.
  return np.repeat(np.tile([3.0, -3.0], int(seconds / 0.2)), 10)


@pytest.mark.parametrize("v_ego", (FULL_SPEED, 10.0, 30.0))
def test_target_is_untouched_at_road_speed(v_ego):
  target = CrawlTarget(DT)
  target.update(5.0, 0.5)
  for desired in jittery_targets(1.0):
    assert target.update(desired, v_ego) == desired


def test_target_jitter_is_attenuated_at_a_crawl():
  target = CrawlTarget(DT)
  smoothed = np.array([target.update(desired, 1.0) for desired in jittery_targets()])
  assert np.ptp(smoothed[200:]) < 6.0 / 4.0


def test_target_still_follows_a_sustained_change():
  target = CrawlTarget(DT)
  target.update(0.0, 1.0)
  for _ in range(250):
    angle = target.update(10.0, 1.0)
  assert angle == pytest.approx(10.0, abs=0.1)


def test_target_restarts_from_the_request_after_a_reset():
  target = CrawlTarget(DT)
  target.update(20.0, 0.5)
  target.reset()
  assert target.update(-4.0, 0.5) == -4.0


def test_authority_is_zero_when_stopped_and_full_above_walking_pace():
  assert crawl_authority(0.0) == 0.0
  assert crawl_authority(CRAWL_AUTHORITY_BP[0]) == 0.0
  assert crawl_authority(CRAWL_AUTHORITY_BP[-1]) == 1.0
  assert crawl_authority(30.0) == 1.0
  speeds = np.linspace(0.0, 3.0, 61)
  assert np.all(np.diff([crawl_authority(v) for v in speeds]) >= 0.0)


def make_controller(monkeypatch, eps_modified=True):
  live = SimpleNamespace(snapshot=make_snapshot(1, NrdrInterpolatedTorquePifBlend=False))
  controller, _, _ = make_live_pid(monkeypatch, live)
  controller.is_eps_modified = eps_modified
  cs = SimpleNamespace(vEgo=25.0, steeringAngleDeg=0.0, steeringRateDeg=0.0,
                       steeringPressed=False, steeringTorque=0.0, leftBlinker=False, rightBlinker=False)
  vehicle = SimpleNamespace(roll=0.0, angleOffsetDeg=0.0, valid=True,
                            angleOffsetValid=True, steerRatioValid=True, stiffnessFactorValid=True)

  def update(desired, v_ego, active=True):
    cs.vEgo = v_ego
    monkeypatch.setattr(controller, "_desired_angles", lambda *args: (desired, desired))
    return controller.update(active, cs, None, vehicle, False, 0.0, None, False, 0.2)

  return controller, update


def test_no_torque_is_requested_at_a_standstill(monkeypatch):
  _, update = make_controller(monkeypatch)
  for v_ego in (0.0, 0.2, CRAWL_AUTHORITY_BP[0]):
    torque, _, pid_log = update(8.0, v_ego)
    assert torque == 0.0
    assert pid_log.active


def test_controller_tracks_the_smoothed_target_at_a_crawl(monkeypatch):
  _, update = make_controller(monkeypatch)
  targets = [update(desired, 1.0)[1] for desired in jittery_targets()]
  assert np.ptp(targets[200:]) < 6.0 / 4.0


def test_engagement_at_a_crawl_starts_from_the_live_request(monkeypatch):
  controller, update = make_controller(monkeypatch)
  update(15.0, 1.0)
  update(15.0, 1.0, active=False)
  assert controller.crawl_target.angle is None
  assert update(-2.0, 1.0)[1] == -2.0


def road_speed_outputs(update):
  rng = np.random.default_rng(0)
  return [update(float(desired), float(rng.uniform(FULL_SPEED, 35.0)))[0] for desired in rng.normal(0.0, 3.0, 400)]


@pytest.mark.parametrize("eps_modified", (True, False))
def test_road_speed_output_matches_the_controller_without_crawl_handling(monkeypatch, eps_modified):
  outputs = road_speed_outputs(make_controller(monkeypatch, eps_modified)[1])
  # Only the reference controller below runs with the crawl handling removed.
  monkeypatch.setattr(latcontrol_pid, "crawl_authority", lambda v_ego: 1.0)
  monkeypatch.setattr(CrawlTarget, "update", lambda self, desired, v_ego: desired)
  assert outputs == road_speed_outputs(make_controller(monkeypatch, eps_modified)[1])
  assert any(outputs)


def test_stock_eps_is_left_alone_at_a_crawl(monkeypatch):
  _, update = make_controller(monkeypatch, eps_modified=False)
  torque, target, _ = update(8.0, 0.0)
  assert target == 8.0
  assert torque != 0.0
