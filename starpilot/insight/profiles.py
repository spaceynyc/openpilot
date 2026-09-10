"""Explicit EPS profiles. A part number by itself never selects a torque mod."""
import math

from openpilot.starpilot.insight.honda_vgr import normalize_honda_eps_firmware
from openpilot.starpilot.insight.settings import capture, encode

# Legacy source: spaceynyc 0d47bde. PTM source: NRDR d2b2df0.
MODIFIED_PROFILES = {
  2: (0.06, 0.02, 0.000024, 3840),
  3: (0.03, 0.01, 0.000012, 4096),
}


def _validated_pid(data):
  if not isinstance(data, dict) or set(data) - {"kpBP", "kpV", "kiBP", "kiV", "kf", "kfBP", "kfV"}:
    raise ValueError("Invalid PID migration fields")
  result = {}
  for term, maximum in (("kp", 1.0), ("ki", 0.5), ("kf", 0.001)):
    if term == "kf" and not data.get("kfBP") and not data.get("kfV"):
      result["kfBP"], result["kfV"] = [], []
      continue
    bp, values = data.get(term + "BP"), data.get(term + "V")
    if not isinstance(bp, list) or not isinstance(values, list) or not 1 <= len(bp) == len(values) <= 16:
      raise ValueError("Invalid PID migration arrays")
    if any(not math.isfinite(float(v)) for v in bp + values):
      raise ValueError("Nonfinite PID migration")
    if bp[0] != 0 or any(b <= a for a, b in zip(bp, bp[1:])) or bp[-1] > 100:
      raise ValueError("Invalid PID speed breakpoints")
    if any(not 0 <= v <= maximum for v in values):
      raise ValueError("Invalid PID migration gain")
    result[term + "BP"], result[term + "V"] = bp, values
  kf = data.get("kf")
  if not isinstance(kf, (float, int)) or not math.isfinite(kf) or not 0 <= kf <= 0.001:
    raise ValueError("Invalid feedforward migration")
  result["kf"] = kf
  return result


def apply_profile(CP, car_fw, params, docs=False) -> str:
  if docs or str(CP.carFingerprint) != "HONDA_INSIGHT" or not params.get("InsightSteeringEnabled"):
    return "baseline"
  profile = params.get("InsightEpsProfile")
  if profile not in (1, 2, 3):
    return "unverified-profile"
  eps_versions = [normalize_honda_eps_firmware(f.fwVersion) for f in car_fw if f.ecu == "eps"]
  if not eps_versions or set(eps_versions) != {"39990-TXM-A040"}:
    return "unknown-eps"
  # Resolve everything before changing CP, so malformed imports leave baseline.
  try:
    settings = capture(params)
    override = params.get("InsightBasePid") or {}
    migrated_pid = _validated_pid(override) if override else None
  except (ValueError, TypeError):
    return "invalid-settings"
  if profile in MODIFIED_PROFILES:
    kp, ki, kf, torque = MODIFIED_PROFILES[profile]
    CP.lateralTuning.init("pid")
    CP.lateralTuning.pid.kpBP, CP.lateralTuning.pid.kpV = [0.0], [kp]
    CP.lateralTuning.pid.kiBP, CP.lateralTuning.pid.kiV = [0.0], [ki]
    CP.lateralTuning.pid.kf = kf
    if profile == 2:
      CP.lateralTuning.pid.kfBP = [0.0, 25 * 0.44704, 50 * 0.44704]
      CP.lateralTuning.pid.kfV = [0.000024, 0.000012, 0.000012]
    CP.lateralParams.torqueBP, CP.lateralParams.torqueV = [0, torque], [0, torque]
    CP.steerAtStandstill = True
    CP.autoResumeSng = True
    CP.minEnableSpeed = CP.minSteerSpeed = -1.0
  elif CP.lateralTuning.which() != "pid":
    # An explicit stock Insight PID profile also wins over generic torque/NNFF
    # toggles; use the unchanged stable stock PID values.
    CP.lateralTuning.init("pid")
    CP.lateralTuning.pid.kpBP, CP.lateralTuning.pid.kpV = [0.0], [0.6]
    CP.lateralTuning.pid.kiBP, CP.lateralTuning.pid.kiV = [0.0], [0.18]
    CP.lateralTuning.pid.kf = 0.00006
  if migrated_pid:
    for key, value in migrated_pid.items():
      setattr(CP.lateralTuning.pid, key, value)
  CP.insightTuning = encode(profile, settings)
  return "selected"
