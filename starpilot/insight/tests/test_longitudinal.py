"""Exercise the actual stable planner and rebuilt native MPC, without host redirects."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import types

import numpy as np
import pytest

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.lib.blotv2 import model_predicted_acceleration
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.tests.test_longitudinal_planner import make_sm, make_lead, make_toggles

ROOT = Path(__file__).resolve().parents[3]
BASE = '1c35e376e9e427431c83dd199ad7d1bdb0044975'


class FreshSM(dict):
  def __init__(self, *args):
    super().__init__(*args)
    self.valid = {s: True for s in ('carState', 'radarState', 'modelV2')}
    self.alive = self.valid.copy()
    self.recv_time = {s: 100.0 for s in self.valid}


def cp():
  result = CarInterface.get_non_essential_params(CAR.HONDA_INSIGHT)
  result.openpilotLongitudinalControl = True
  return result


def scene(v=10., lead_v=8., lead_a=-0.6, distance=40., experimental=False):
  lead = make_lead(status=True, d_rel=distance, v_lead=lead_v, a_lead=lead_a, radar=True, model_prob=0.99)
  lead.radarTrackId = 1
  sm = FreshSM(make_sm(v, 0., -3.5, lead_one=lead, tracking_lead=True, experimental_mode=experimental))
  return sm


@pytest.fixture
def clock(monkeypatch):
  monkeypatch.setattr(time, 'monotonic', lambda: 100.)


@pytest.fixture(scope='module')
def baseline():
  file = ROOT / '.insight-baseline-planner.py'
  data = file.read_bytes() if file.exists() else subprocess.check_output([
    'git', 'show', f'{BASE}:selfdrive/controls/lib/longitudinal_planner.py'], cwd=ROOT)
  # Filled with the pinned source's SHA-256, independent of a mutable working tree.
  assert hashlib.sha256(data).hexdigest() == '4d1ac84aa8448ba9dd179bb94a0d7038cbf58e48a7e99d92cfedf6ecb642b973'
  module = types.ModuleType('insight_pinned_baseline')
  module.__file__ = str(file)
  sys.modules[module.__name__] = module
  exec(compile(data, str(file), 'exec'), module.__dict__)
  return module.LongitudinalPlanner


@pytest.mark.parametrize('personality', range(4))
@pytest.mark.parametrize('experimental,version', [(False, 'v10'), (True, 'v10'), (True, 'v11')])
@pytest.mark.parametrize('scenario', ['slowing', 'stopped', 'pulling-away', 'inch-stop', 'cut-in', 'hard-brake', 'disappear', 'disengage'])
def test_off_matches_pinned_stable_solver(clock, baseline, personality, experimental, version, scenario):
  config = cp()
  current = LongitudinalPlanner(config, init_v=10.)
  current.insight_policy.enabled = False
  original = baseline(config, init_v=10.)
  sm = scene(experimental=experimental)
  sm['selfdriveState'].personality = personality
  sm['starpilotPlan'].tFollow = (1.2, 1.45, 1.8, 2.1)[personality]
  for frame in range(16):
    lead = sm['radarState'].leadOne
    if scenario == 'stopped':
      lead.vLead = lead.vLeadK = 0.
    elif scenario == 'pulling-away':
      lead.vLead = lead.vLeadK = 12.
      lead.aLeadK = 1.
    elif scenario == 'inch-stop':
      sm['carState'].vEgo = 0.3
      sm['carState'].vEgoCluster = 0.3
      sm['carState'].standstill = frame >= 8
      lead.dRel = 6.5
      lead.vLead = lead.vLeadK = 0.6 if frame < 8 else 0.
      lead.aLeadK = 0.8 if frame < 8 else -1.
    elif scenario == 'cut-in':
      lead.dRel = 15. if frame >= 8 else 45.
      lead.radarTrackId = 2 if frame >= 8 else 1
    elif scenario == 'hard-brake':
      lead.dRel = 20.
      lead.aLeadK = -5.
    elif scenario == 'disappear':
      lead.status = frame < 8
    elif scenario == 'disengage':
      sm['controlsState'].longControlState = LongCtrlState.off if 5 <= frame < 10 else LongCtrlState.pid
    original.update(sm, make_toggles(version))
    current.update(sm, make_toggles(version))
    np.testing.assert_array_equal(current.mpc.a_solution, original.mpc.a_solution)
    np.testing.assert_array_equal(current.a_desired_trajectory, original.a_desired_trajectory)
    assert current.output_a_target == original.output_a_target
    assert current.output_should_stop == original.output_should_stop
    assert current.mpc.mode == original.mpc.mode


@pytest.mark.parametrize('reason', ['stale', 'lost', 'disengage', 'blended', 'model-sim', 'identity', 'disabled'])
def test_active_policy_resets_without_stale_latches(clock, reason):
  planner = LongitudinalPlanner(cp(), init_v=10.)
  policy = planner.insight_policy
  policy.enabled = True
  sm = scene(lead_v=13., lead_a=3.)
  for _ in range(20):
    planner.update(sm, make_toggles('v10'))
  assert policy.policy is not None and policy.policy.launch_active
  assert policy.supervisor.jerk_scale < 1.
  toggles = make_toggles('v10')
  if reason == 'stale': sm.recv_time['radarState'] = 99.
  elif reason == 'lost': sm['radarState'].leadOne.status = False
  elif reason == 'disengage': sm['controlsState'].longControlState = LongCtrlState.off
  elif reason in ('blended', 'model-sim'):
    sm['selfdriveState'].experimentalMode = True
    toggles = make_toggles('v11' if reason == 'model-sim' else 'v10')
  elif reason == 'identity': sm['radarState'].leadOne.radarTrackId = 2
  elif reason == 'disabled': policy.enabled = False
  planner.update(sm, toggles)
  assert policy.supervisor.jerk_scale == 1.
  assert policy.supervisor.t_follow_pad == 0.
  assert policy.policy is None or not policy.policy.launch_active


@pytest.mark.parametrize('personality', range(4))
def test_on_pad_applied_once_and_cost_reaches_actual_solver(clock, personality, monkeypatch):
  planner = LongitudinalPlanner(cp(), init_v=10.)
  planner.insight_policy.enabled = True
  sm = scene()
  sm['modelV2'].leadsV3[0].prob = .99
  sm['modelV2'].leadsV3[0].v = [8., 6.]
  observed_weights = []
  real_set_weights = planner.mpc.set_weights
  def capture_weights(*args, **kwargs):
    observed_weights.append(args[0])
    return real_set_weights(*args, **kwargs)
  monkeypatch.setattr(planner.mpc, 'set_weights', capture_weights)
  sm['selfdriveState'].personality = personality
  sm['starpilotPlan'].tFollow = (1.2, 1.45, 1.8, 2.1)[personality]
  for _ in range(20):
    planner.update(sm, make_toggles('v10'))
  policy = planner.insight_policy.policy
  base = planner.effective_t_follow
  assert policy is not None and planner.mpc.mode == 'acc'
  assert base <= policy.t_follow <= base + .75
  assert policy.t_follow == pytest.approx(base + planner.insight_policy.supervisor.t_follow_pad)
  assert .3 <= policy.jerk_scale < 1.
  assert observed_weights[-1] == pytest.approx(sm['starpilotPlan'].accelerationJerk * policy.jerk_scale)
  # These are the actual generated solver parameters, including the padded follow time.
  assert planner.mpc.params[0, 4] == pytest.approx(policy.t_follow)
  assert np.all(np.isfinite(planner.mpc.a_solution))
  assert json.loads(planner.insight_policy.diagnostics())['active']


def test_inching_then_stopping_does_not_arm_launch(clock):
  planner = LongitudinalPlanner(cp(), init_v=.3)
  planner.insight_policy.enabled = True
  sm = scene(v=.3, lead_v=.6, lead_a=.8, distance=6.5)
  for frame in range(40):
    if frame >= 8:
      sm['radarState'].leadOne.vLeadK = sm['radarState'].leadOne.vLead = 0.
      sm['radarState'].leadOne.aLeadK = -1.
    planner.update(sm, make_toggles('v10'))
    policy = planner.insight_policy.policy
    assert policy is not None and not policy.launch_active
    assert policy.jerk_scale == 1.
    assert math.isfinite(planner.output_a_target)
    assert planner.output_a_target <= sm['starpilotPlan'].maxAcceleration


@pytest.mark.parametrize('prob', [float('nan'), float('inf'), -1.])
def test_reject_nonfinite_forecast_confidence(prob):
  assert model_predicted_acceleration(types.SimpleNamespace(prob=prob, v=[10., 9.])) is None
