"""Insight geometry shared by measured and desired curvature (NRDR d2b2df0)."""
import math

import numpy as np

from openpilot.starpilot.insight.honda_vgr import get_honda_vgr_profile


class InsightGeometry:
  def __init__(self, CP, settings):
    self.cp_ratio = float(CP.steerRatio)
    self.mode = settings["NrdrSteerRatioMode"]
    self.center = settings["NrdrSteerRatioManualCenter"]
    self.outer = settings["NrdrSteerRatioManualFinal"]
    self.outer_angle = 2.54 * 250.0 / 2.41
    self.firmware = get_honda_vgr_profile(CP)
    self.unavailable_reason = ""
    if self.mode == 2:
      self.unavailable_reason = "No audited raw-angle curve for Insight"
    elif self.mode == 3 and self.firmware is None:
      self.unavailable_reason = "No recognized Insight EPS firmware geometry"
    self.learned_ratio = self.cp_ratio
    self.active = False

  def update(self, VM, measured_angle, learned_ratio, active):
    if not self.active:
      self.learned_ratio = learned_ratio if math.isfinite(learned_ratio) and 8 <= learned_ratio <= 25 else self.cp_ratio
    self.active = active
    if self.unavailable_reason or self.mode == 3:
      ratio = self.cp_ratio
    elif self.mode == 0:
      ratio = float(np.interp(abs(measured_angle) if math.isfinite(measured_angle) else 0.0, (0.0, self.outer_angle), (self.center, self.outer)))
    else:
      ratio = self.learned_ratio
    VM.sR = ratio

  @property
  def firmware_selected(self):
    return self.mode == 3 and self.firmware is not None

  def measured_curvature(self, VM, CS, params):
    if not all(math.isfinite(v) for v in (CS.steeringAngleDeg, params.angleOffsetDeg, CS.vEgo, params.roll)):
      return 0.0
    angle = CS.steeringAngleDeg - params.angleOffsetDeg
    if self.firmware_selected:
      angle = self.firmware.physical_to_linear(angle)
    return -VM.calc_curvature(math.radians(angle), CS.vEgo, params.roll)

  def desired_angle(self, VM, CS, params, desired_curvature):
    angle = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
    if self.firmware_selected:
      angle = self.firmware.linear_to_physical(angle)
    return angle
