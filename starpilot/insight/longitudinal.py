"""Validity/lifecycle adapter for James's bounded BLoTv2 supervisor."""
import json
import math
import time

from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.blotv2 import BLoTv2Supervisor, model_predicted_acceleration
from openpilot.selfdrive.controls.lib.longitudinal_lead import LeadObservation


class InsightLongitudinalPolicy:
  def __init__(self, CP, dt):
    self.enabled = bool(Params().get_bool('BlotV2'))
    self.openpilot_long = CP.openpilotLongitudinalControl
    self.supervisor = BLoTv2Supervisor(dt)
    self.policy = None
    self.reason = 'disabled'
    self.mode = 'unknown'
    self.model_simulation = False
    self.track = None

  @staticmethod
  def fresh(sm, now):
    try:
      return all(sm.valid[s] and sm.alive[s] and 0 <= now - sm.recv_time[s] <= 0.25
                 for s in ('carState', 'radarState', 'modelV2'))
    except (AttributeError, KeyError, TypeError):
      return False

  def reset(self, reason):
    self.supervisor.reset()
    self.policy = None
    self.track = None
    self.reason = reason
    return None

  def update(self, sm, mode, model_simulation, reset_state, lead_active, v_ego, a_mpc, t_follow):
    self.mode, self.model_simulation = mode, model_simulation
    if not self.enabled:
      return self.reset('disabled')
    if not self.openpilot_long or reset_state or not sm['selfdriveState'].enabled:
      return self.reset('disengaged')
    if mode != 'acc' or model_simulation:
      return self.reset('unsupported-mode')
    if not self.fresh(sm, time.monotonic()):
      return self.reset('stale-input')
    if not all(math.isfinite(x) for x in (v_ego, a_mpc, t_follow)) or not 0.1 <= t_follow <= 5.0:
      return self.reset('invalid-input')
    raw_lead = sm['radarState'].leadOne
    lead = LeadObservation.from_radar(raw_lead, lead_active)
    if not lead.present:
      return self.reset('no-lead')
    # Never carry a launch/braking latch across a radar identity change.
    track = (bool(raw_lead.radar), int(raw_lead.radarTrackId))
    if self.track != track:
      self.supervisor.reset()
      self.track = track
    leads = sm['modelV2'].leadsV3
    forecast = model_predicted_acceleration(leads[0]) if len(leads) else None
    self.policy = self.supervisor.update(lead, v_ego, a_mpc, t_follow, forecast)
    self.reason = 'active'
    return self.policy

  def diagnostics(self):
    policy = self.policy
    return json.dumps({
      'schema': 1, 'enabled': self.enabled, 'mode': self.mode, 'modelSimulation': self.model_simulation,
      'reason': self.reason, 'active': policy is not None,
      'jerkScale': policy.jerk_scale if policy else 1.0,
      'tFollowPad': self.supervisor.t_follow_pad,
      'emergency': policy.emergency if policy else False,
      'launch': policy.launch_active if policy else False,
      'recovery': policy.recovery_active if policy else False,
      'modelForecast': policy.model_active if policy else False,
    }, sort_keys=True, separators=(',', ':'), allow_nan=False)
