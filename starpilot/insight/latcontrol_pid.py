"""Insight-only NRDR PID adapter for StarPilot's controller signature.

Retains stock driver override and final torque limits. Optional torque/PIF
blending, tune learning, injection testing and override tolerance are excluded.
"""
import math

import numpy as np

from cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.pid import PIDController
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.starpilot.insight.geometry import InsightGeometry
from openpilot.starpilot.insight.diagnostics import steering_report
from openpilot.starpilot.insight.lat_stiction import LatStiction
from openpilot.starpilot.insight.phase_detector import phase_with_latch
from openpilot.starpilot.insight.settings import decode, speed_band

MPH_TO_MS = 0.44704


def center_boost(angle, speed, fade, settings):
  angle_weight = float(np.clip(settings["HondaCenterBoostThreshold"] + 1.0 - abs(angle), 0.0, 1.0))
  minimum = settings["HondaCenterBoostMinSpeed"] * MPH_TO_MS
  speed_weight = float(np.clip((speed - minimum) / (5 * MPH_TO_MS), 0.0, 1.0)) if minimum > 0 else 1.0
  return 1.0 + angle_weight * settings["HondaCenterScale"] * fade * speed_weight


class InsightLatControlPID(LatControl):
  @staticmethod
  def supports(CP):
    return decode(CP) is not None

  def __init__(self, CP, CI, dt):
    super().__init__(CP, CI, dt)
    decoded = decode(CP)
    if decoded is None:
      raise ValueError("Missing Insight PID configuration")
    self.profile, self.settings = decoded
    self.diagnostic_report = steering_report(CP, type(self).__name__)
    self.modified = self.profile in (2, 3)
    self.geometry = InsightGeometry(CP, self.settings)
    tune = CP.lateralTuning.pid
    self.pid = PIDController((tune.kpBP, tune.kpV), (tune.kiBP, tune.kiV),
                             pos_limit=self.steer_max, neg_limit=-self.steer_max, rate=1.0 / dt)
    self.kf = float(tune.kf)
    self.kf_bp, self.kf_v = list(tune.kfBP), list(tune.kfV)
    self.feedforward = CI.get_steer_feedforward_function()
    self.center_taper = FirstOrderFilter(1.0, 0.25, dt)
    self.stiction = LatStiction(dt, self.steer_max)
    self.previous_desired = 0.0
    self.previous_output = 0.0
    self.previous_saturated = False
    self.phase_direction = 0.0

  def measured_curvature(self, CS, VM, params, active):
    self.geometry.update(VM, CS.steeringAngleDeg, params.steerRatio, active)
    return self.geometry.measured_curvature(VM, CS, params)

  def reset(self):
    super().reset()
    self.pid.reset()
    self.stiction.reset()
    self.center_taper.x = 1.0
    self.previous_output = 0.0
    self.previous_saturated = False
    self.phase_direction = 0.0

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature,
             curvature_limited, lat_delay, calibrated_pose, model_data, starpilot_toggles):
    pid_log = log.ControlsState.LateralPIDState.new_message()
    finite = all(math.isfinite(float(value)) for value in (
      CS.vEgo, CS.steeringAngleDeg, CS.steeringRateDeg, params.angleOffsetDeg, params.roll, desired_curvature,
    ))
    if not finite:
      self.reset()
      return 0.0, 0.0, pid_log
    desired_no_offset = self.geometry.desired_angle(VM, CS, params, desired_curvature)
    desired = desired_no_offset + params.angleOffsetDeg
    if not math.isfinite(desired):
      self.reset()
      return 0.0, 0.0, pid_log
    error = desired - CS.steeringAngleDeg
    pid_log.steeringAngleDeg = float(CS.steeringAngleDeg)
    pid_log.steeringRateDeg = float(CS.steeringRateDeg)
    pid_log.steeringAngleDesiredDeg = float(desired)
    pid_log.angleError = float(error)
    if not math.isfinite(desired) or not active or CS.steeringPressed or CS.steerFaultTemporary or CS.steerFaultPermanent:
      self.reset()
      self.previous_desired = desired_no_offset if math.isfinite(desired_no_offset) else 0.0
      return 0.0, float(desired) if math.isfinite(desired) else 0.0, pid_log

    angle_delta = desired_no_offset - self.previous_desired
    phase, self.phase_direction = phase_with_latch(desired_no_offset, angle_delta, CS.vEgo, self.phase_direction)
    freeze = steer_limited_by_safety or CS.vEgo < (2.0 if self.modified else 5.0)
    freeze |= self.previous_saturated and error * self.previous_output > 0
    freeze |= phase < 0 and self.pid.i * error > 0
    freeze |= self.settings["NrdrLatStiction"] and self.stiction.freeze_integrator
    factor = float(np.interp(CS.vEgo, self.kf_bp, self.kf_v)) if self.kf_v else self.kf
    self.pid.update(error, feedforward=factor * self.feedforward(desired_no_offset, CS.vEgo), speed=CS.vEgo, freeze_integrator=freeze)
    current_band = speed_band(CS.vEgo)
    p_term = self.pid.p * self.settings[f"LatPScale{current_band}"] / 100
    i_term = self.pid.i * self.settings[f"LatIScale{current_band}"] / 100
    f_term = self.pid.f * self.settings[f"LatFScale{current_band}"] / 100
    if self.modified:
      blinking = CS.leftBlinker or CS.rightBlinker
      if blinking:
        self.center_taper.x = 0.0
      fade = 0.0 if blinking else float(self.center_taper.update(1.0))
      p_term *= center_boost(desired_no_offset, CS.vEgo, fade, self.settings)
    output = p_term + i_term + self.pid.d + f_term
    if self.modified:
      fade_speed = self.settings["NrdrLatRateDampingFadeSpeed"] * MPH_TO_MS
      speed_fade = float(np.clip((fade_speed - CS.vEgo) / fade_speed, 0, 1)) if fade_speed > 0 else 0.0
      unwind_weight = float(np.clip(-phase / 0.5, 0, 1))
      angle_fade = float(np.clip((30 - abs(CS.steeringAngleDeg)) / 30, 0, 1))
      output -= self.settings["NrdrLatRateDamping"] / 100 * 0.010 * CS.steeringRateDeg * speed_fade * (1 - unwind_weight + unwind_weight * angle_fade)
    if not math.isfinite(output):
      self.reset()
      return 0.0, float(desired), pid_log
    limited = bool(curvature_limited or steer_limited_by_safety or abs(output) >= self.steer_max - 1e-3)
    output = float(np.clip(output, -self.steer_max, self.steer_max))
    if self.settings["NrdrLatStiction"]:
      lane_change = model_data is not None and model_data.meta.laneChangeState != log.LaneChangeState.off
      output = self.stiction.update(True, CS.vEgo, error, angle_delta / self.dt, CS.steeringRateDeg,
                                    output, CS.steeringPressed, lane_change, limited or self.previous_saturated)
    else:
      self.stiction.reset()
    output = float(np.clip(output, -self.steer_max, self.steer_max))
    self.previous_saturated = abs(output) >= self.steer_max - 1e-3
    pid_log.active = True
    pid_log.p, pid_log.i, pid_log.f = float(p_term), float(i_term), float(f_term)
    pid_log.output = output
    pid_log.saturated = bool(self._check_saturation(self.previous_saturated, CS, steer_limited_by_safety, curvature_limited))
    self.previous_output, self.previous_desired = output, desired_no_offset
    return output, float(desired), pid_log
