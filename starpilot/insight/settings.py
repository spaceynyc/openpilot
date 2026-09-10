"""Bounded Insight settings, captured as one immutable CarParams snapshot.

The key names retain NRDR's units. Editing them takes effect next ignition;
controlsd and card consume the identical serialized snapshot, never live Params.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType


@dataclass(frozen=True)
class Setting:
  kind: str
  default: float | int | bool
  minimum: float = 0
  maximum: float = 1
  label: str = ""
  step: float = 1


SETTINGS = {
  **{f"Lat{term}Scale{band}": Setting("int", 100, 0, 500, f"{term} at {label}", 5)
     for term in ("P", "I", "F") for band, label in (("LowSpeed", "0–25 mph"), ("Standard", "25–50 mph"), ("Highway", "50+ mph"))},
  "HondaCenterScale": Setting("float", 0.0, 0, 5, "Center compensation", 0.05),
  "HondaCenterBoostThreshold": Setting("float", 3.0, 0, 10, "Center angle threshold", 0.1),
  "HondaCenterBoostMinSpeed": Setting("int", 50, 0, 90, "Center minimum speed (mph)", 5),
  "NrdrLatRateDamping": Setting("int", 0, 0, 300, "Rate damping (%)", 5),
  "NrdrLatRateDampingFadeSpeed": Setting("int", 30, 0, 60, "Damping fade speed (mph)", 5),
  "NrdrLatStiction": Setting("bool", False, label="Stiction compensation"),
  "HondaTorqueLowPassFilter": Setting("bool", False, label="Steering output smoothing"),
  "HondaLpfTauLowSpeed": Setting("float", 0.10, 0, 1, "Smoothing at 0–25 mph (s)", 0.01),
  "HondaLpfTauStandard": Setting("float", 0.10, 0, 1, "Smoothing at 25–50 mph (s)", 0.01),
  "HondaLpfTauHighway": Setting("float", 0.05, 0, 1, "Smoothing at 50+ mph (s)", 0.01),
  "HondaSteerDeltaLimiter": Setting("bool", False, label="Additional steering rate limit"),
  "HondaSteerDeltaUp": Setting("float", 3.0, 0.1, 3, "Steering increase per second", 0.1),
  "HondaSteerDeltaDown": Setting("float", 3.0, 0.1, 3, "Steering decrease per second", 0.1),
  # 2 retains the donor enum but has no Insight raw curve and is not offered.
  "NrdrSteerRatioMode": Setting("int", 1, 0, 3, "Geometry: 0 manual, 1 learned, 3 firmware"),
  "NrdrSteerRatioManualCenter": Setting("float", 15.38, 8, 25, "Manual center steering ratio", 0.01),
  "NrdrSteerRatioManualFinal": Setting("float", 10.93, 8, 25, "Manual outer steering ratio", 0.01),
}
PROFILE_LABELS = {0: "Unverified / StarPilot baseline", 1: "Stock EPS", 2: "Legacy linear-max EPS", 3: "PTM EPS"}


def bounded(value, spec: Setting):
  if spec.kind == "bool":
    return value in (True, 1, "1", b"1")
  try:
    number = float(value)
  except (TypeError, ValueError):
    return spec.default
  if not math.isfinite(number):
    return spec.default
  number = min(max(number, spec.minimum), spec.maximum)
  return int(number) if spec.kind == "int" else float(number)


def capture(params) -> dict:
  # Two identical reads prevent a single torn settings batch becoming the
  # ignition snapshot. Failure to settle disables the adapter for this start.
  for _ in range(3):
    first = {key: params.get(key) for key in SETTINGS}
    second = {key: params.get(key) for key in SETTINGS}
    if first == second:
      return {key: bounded(value, SETTINGS[key]) for key, value in first.items()}
  raise ValueError("Insight settings changed while capturing CarParams")


def encode(profile: int, values: dict) -> str:
  return json.dumps({"schema": 1, "profile": profile, "settings": values}, sort_keys=True, separators=(",", ":"))


def decode(CP):
  if str(CP.carFingerprint) != "HONDA_INSIGHT" or CP.lateralTuning.which() != "pid":
    return None
  try:
    data = json.loads(CP.insightTuning)
    if data["schema"] != 1 or data["profile"] not in (1, 2, 3):
      return None
    values = {key: bounded(data["settings"].get(key), spec) for key, spec in SETTINGS.items()}
    return data["profile"], MappingProxyType(values)
  except (AttributeError, TypeError, KeyError, ValueError):
    return None


def digest(CP) -> str:
  return hashlib.sha256(CP.insightTuning.encode()).hexdigest()[:16]


def speed_band(v_ego):
  return "LowSpeed" if v_ego < 25 * 0.44704 else "Standard" if v_ego < 50 * 0.44704 else "Highway"


def validate_edit(key, value):
  """Strict settings-interface validation; runtime capture also clamps defensively."""
  if key == 'InsightEpsProfile':
    if isinstance(value, bool) or str(value) not in ('0', '1', '2', '3'):
      raise ValueError('Select an explicit EPS profile from the list')
  elif key == 'NrdrSteerRatioMode':
    if isinstance(value, bool) or str(value) not in ('0', '1', '3'):
      raise ValueError('Insight geometry supports manual, learned or firmware mode')
  elif key in SETTINGS:
    spec = SETTINGS[key]
    if spec.kind == 'bool':
      if value not in (True, False, 0, 1, '0', '1'):
        raise ValueError(f'{key} must be enabled or disabled')
    else:
      try:
        numeric = float(value)
      except (TypeError, ValueError):
        raise ValueError(f'{key} must be numeric') from None
      if not math.isfinite(numeric) or numeric != bounded(value, spec):
        raise ValueError(f'{key} must be between {spec.minimum} and {spec.maximum}')
