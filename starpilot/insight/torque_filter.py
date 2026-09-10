"""Steering-only extraction of NRDR Honda output filtering, before stock limits."""
import math

from openpilot.starpilot.insight.settings import decode, speed_band


class InsightTorqueFilter:
  def __init__(self, CP, dt):
    decoded = decode(CP)
    self.settings = decoded[1] if decoded else None
    self.dt = dt
    self.output = self.filtered = 0.0

  def update(self, command, v_ego, active, steering_pressed):
    if self.settings is None:
      return command
    if not active or steering_pressed or not math.isfinite(command) or not math.isfinite(v_ego):
      self.output = self.filtered = 0.0
      return 0.0
    if self.settings["HondaTorqueLowPassFilter"]:
      tau = self.settings[f"HondaLpfTau{speed_band(v_ego)}"]
      alpha = self.dt / (tau + self.dt)
      self.filtered += alpha * (command - self.filtered)
      command = self.filtered
    else:
      self.filtered = command
    if self.settings["HondaSteerDeltaLimiter"]:
      command = min(max(command, self.output - self.settings["HondaSteerDeltaDown"] * self.dt),
                    self.output + self.settings["HondaSteerDeltaUp"] * self.dt)
    self.output = min(max(command, -1.0), 1.0)
    return self.output
