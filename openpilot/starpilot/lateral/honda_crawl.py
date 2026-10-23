"""Calm Honda modified-EPS steering at a crawl and at a stop. Ported from nrdr's crawl_steering.

Modified EPS steers down to 0 mph. Below walking pace the tyres scrub instead of rolling, the model's curvature
noise is several degrees of steering target, and the P-only loop (the integrator is frozen below 5 m/s) stick-slips
the wheel against that scrub, felt as grinding while coming to a stop. At a standstill it keeps pushing on tyres
that cannot turn.
"""

import numpy as np

from opendbc.car.honda.values import HondaFlags

# Low-pass the steering target at a crawl; an exact pass-through from 4 m/s (9 mph) up.
CRAWL_TARGET_TAU_BP = (1.0, 4.0)
CRAWL_TARGET_TAU = (0.5, 0.0)
# No torque into stationary tyres; full authority from 1.5 m/s (3.4 mph) up.
CRAWL_AUTHORITY_BP = (0.3, 1.5)
CRAWL_AUTHORITY = (0.0, 1.0)


def eligible(CP) -> bool:
  return CP.brand == "honda" and CP.carFingerprint == "HONDA_INSIGHT" and bool(CP.flags & HondaFlags.EPS_MODIFIED)


def crawl_authority(v_ego: float) -> float:
  return float(np.interp(v_ego, CRAWL_AUTHORITY_BP, CRAWL_AUTHORITY))


class CrawlTarget:
  def __init__(self, dt: float):
    self.dt = dt
    self.angle = None

  def reset(self) -> None:
    self.angle = None

  def update(self, desired_angle: float, v_ego: float) -> float:
    tau = float(np.interp(v_ego, CRAWL_TARGET_TAU_BP, CRAWL_TARGET_TAU))
    if self.angle is None or not tau > 0.0:
      self.angle = float(desired_angle)
    else:
      self.angle += self.dt / (tau + self.dt) * (float(desired_angle) - self.angle)
    return self.angle
