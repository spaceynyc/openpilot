from bisect import bisect_right
from dataclasses import dataclass, field
import math

import numpy as np


__all__ = (
  "HONDA_VGR_PROFILES",
  "Q14",
  "RAW_UNITS_PER_DEGREE",
  "HondaVgrProfile",
  "get_honda_vgr_profile",
  "normalize_honda_eps_firmware",
)


Q14 = 1 << 14
RAW_UNITS_PER_DEGREE = 10.0


@dataclass(frozen=True)
class HondaVgrProfile:
  name: str
  fingerprints: tuple[str, ...]
  firmware: tuple[str, ...]
  position_x: tuple[int, ...]
  position_y: tuple[int, ...]
  physical_knots: tuple[float, ...] = field(init=False)

  def __post_init__(self) -> None:
    if len(self.position_x) != len(self.position_y) or len(self.position_x) < 2:
      raise ValueError("Honda VGR position tables must contain matching X/Y values")
    if self.position_x[0] != 0 or any(b <= a for a, b in zip(self.position_x, self.position_x[1:], strict=False)):
      raise ValueError("Honda VGR position breakpoints must start at zero and increase")
    if any(value <= 0 for value in self.position_y):
      raise ValueError("Honda VGR position divisors must be positive")

    physical = tuple(raw * Q14 / (divisor * RAW_UNITS_PER_DEGREE)
                     for raw, divisor in zip(self.position_x, self.position_y, strict=True))
    if any(b <= a for a, b in zip(physical, physical[1:], strict=False)):
      raise ValueError("Honda VGR physical-angle map must increase")
    object.__setattr__(self, "physical_knots", physical)

  @property
  def center_divisor(self) -> int:
    return self.position_y[0]

  @property
  def tail_scale(self) -> float:
    return self.center_divisor / self.position_y[-1]

  def linear_to_physical(self, angle_deg: float) -> float:
    raw = abs(angle_deg) * self.center_divisor * RAW_UNITS_PER_DEGREE / Q14
    divisor = float(np.interp(raw, self.position_x, self.position_y))
    physical = raw * Q14 / (divisor * RAW_UNITS_PER_DEGREE)
    return math.copysign(physical, angle_deg)

  def linear_to_physical_slope(self, angle_deg: float) -> float:
    """Return d(physical angle) / d(linear angle) for the active table segment."""
    raw = abs(angle_deg) * self.center_divisor * RAW_UNITS_PER_DEGREE / Q14
    if raw >= self.position_x[-1]:
      return self.tail_scale

    index = min(bisect_right(self.position_x, raw) - 1, len(self.position_x) - 2)
    x0, x1 = self.position_x[index:index + 2]
    y0, y1 = self.position_y[index:index + 2]
    divisor_slope = (y1 - y0) / (x1 - x0)
    divisor = y0 + divisor_slope * (raw - x0)
    divisor_intercept = y0 - divisor_slope * x0
    return self.center_divisor * divisor_intercept / divisor ** 2

  def physical_to_linear(self, angle_deg: float) -> float:
    physical = abs(angle_deg)
    if physical == 0.0:
      return 0.0

    if physical >= self.physical_knots[-1]:
      raw = physical * self.position_y[-1] * RAW_UNITS_PER_DEGREE / Q14
    else:
      index = min(bisect_right(self.physical_knots, physical) - 1, len(self.position_x) - 2)
      x0, x1 = self.position_x[index:index + 2]
      y0, y1 = self.position_y[index:index + 2]
      slope = (y1 - y0) / (x1 - x0)
      intercept = y0 - slope * x0
      raw = physical * RAW_UNITS_PER_DEGREE * intercept / (Q14 - physical * RAW_UNITS_PER_DEGREE * slope)

    linear = raw * Q14 / (self.center_divisor * RAW_UNITS_PER_DEGREE)
    return math.copysign(linear, angle_deg)

# Insight Table-A position map traced by vote_for_nobody, via NRDR d2b2df0.
_TXM_A040_X = (0, 43, 88, 130, 175, 219, 263, 306, 351, 441, 671, 914, 1166, 1297, 1348,
               1401, 1454, 1507, 1559, 1613, 1664, 1718, 1771, 1823, 2217, 2610, 3005, 3402, 3798, 5515)
_TXM_A040_Y = (17613, 17613, 18022, 17886, 17971, 17981, 17988, 17964, 18022, 18084, 18269, 18631,
               19026, 19226, 19313, 19387, 19442, 19523, 19583, 19636, 19692, 19745, 19798, 19839,
               20113, 20317, 20474, 20593, 20702, 20989)


HONDA_VGR_PROFILES = (
  HondaVgrProfile("Insight TXM-A040", ("HONDA_INSIGHT",), ("39990-TXM-A040",), _TXM_A040_X, _TXM_A040_Y),
)
_PROFILE_BY_CAR_FIRMWARE = {("HONDA_INSIGHT", "39990-TXM-A040"): HONDA_VGR_PROFILES[0]}


def normalize_honda_eps_firmware(version) -> str:
  if not isinstance(version, (bytes, str)):
    try:
      version = bytes(version)
    except (TypeError, ValueError):
      pass
  if isinstance(version, bytes):
    version = version.split(b"\0", 1)[0].decode("ascii", errors="ignore")
  return str(version).split("\0", 1)[0].strip().replace(",", "-")


def get_honda_vgr_profile(CP) -> HondaVgrProfile | None:
  if str(getattr(CP, "brand", "")).lower() != "honda":
    return None
  fingerprint = str(getattr(CP, "carFingerprint", ""))
  for firmware in getattr(CP, "carFw", ()):
    if firmware.ecu != "eps":
      continue
    profile = _PROFILE_BY_CAR_FIRMWARE.get((fingerprint, normalize_honda_eps_firmware(firmware.fwVersion)))
    if profile is not None:
      return profile
  return None
