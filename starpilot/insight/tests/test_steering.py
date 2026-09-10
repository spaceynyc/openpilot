import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from cereal import car
from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.starpilot.insight.geometry import InsightGeometry
from openpilot.starpilot.insight.honda_vgr import HONDA_VGR_PROFILES
from openpilot.starpilot.insight.latcontrol_pid import InsightLatControlPID
from openpilot.starpilot.insight.profiles import apply_profile
from openpilot.starpilot.insight.settings import SETTINGS, encode, decode
from openpilot.starpilot.insight.torque_filter import InsightTorqueFilter


class Settings:
  def __init__(self, **overrides):
    self.values = {key: spec.default for key, spec in SETTINGS.items()}
    self.values.update(InsightSteeringEnabled=True, InsightEpsProfile=3)
    self.values.update(overrides)
  def get(self, key):
    return self.values.get(key)


def eps(version=b"39990-TXM,A040\x00\x00"):
  fw = car.CarParams.CarFw.new_message()
  fw.ecu, fw.fwVersion = "eps", version
  return fw


def stock_cp(candidate=CAR.HONDA_INSIGHT):
  return CarInterface.get_params(candidate, gen_empty_fingerprint(), [], False, False, True, SimpleNamespace())


def candidate_cp(**overrides):
  cp = stock_cp()
  fw = eps()
  assert apply_profile(cp, [fw], Settings(**overrides)) == "selected"
  cp.carFw = [fw]
  return cp


def inputs():
  cs = car.CarState.new_message()
  cs.vEgo = 15.0
  lp = SimpleNamespace(steerRatio=15.0, stiffnessFactor=1.0, angleOffsetDeg=0.2, roll=0.01)
  return cs, lp


def controller(cp):
  ci = SimpleNamespace(get_steer_feedforward_function=lambda: lambda angle, speed: angle * speed ** 2)
  return InsightLatControlPID(cp, ci, 0.01)


@pytest.mark.parametrize("profile,gains,torque", [(1, (0.6, 0.18, 0.00006), 4096),
                                               (2, (0.06, 0.02, 0.000024), 3840),
                                               (3, (0.03, 0.01, 0.000012), 4096)])
def test_explicit_profile_and_original_safety_limits(profile, gains, torque):
  baseline = stock_cp()
  cp = candidate_cp(InsightEpsProfile=profile)
  assert cp.lateralTuning.which() == "pid"
  assert (cp.lateralTuning.pid.kpV[0], cp.lateralTuning.pid.kiV[0], cp.lateralTuning.pid.kf) == pytest.approx(gains)
  assert list(cp.lateralParams.torqueV) == [0, torque]
  assert [c.to_dict() for c in cp.safetyConfigs] == [c.to_dict() for c in baseline.safetyConfigs]
  assert cp.alternativeExperience == baseline.alternativeExperience
  assert list(cp.lateralParams.torqueV)[-1] <= list(baseline.lateralParams.torqueV)[-1]


@pytest.mark.parametrize("overrides,firmware,reason", [
  ({"InsightSteeringEnabled": False}, eps(), "baseline"),
  ({"InsightEpsProfile": 0}, eps(), "unverified-profile"),
  ({"InsightEpsProfile": 99}, eps(), "unverified-profile"),
  ({}, eps(b"39990-UNKNOWN"), "unknown-eps"),
  ({"InsightBasePid": {"kpV": [float("nan")]}}, eps(), "invalid-settings"),
])
def test_no_implicit_modified_profile(overrides, firmware, reason):
  cp = stock_cp()
  before = cp.to_dict()
  assert apply_profile(cp, [firmware], Settings(**overrides)) == reason
  assert cp.to_dict() == before
  assert not InsightLatControlPID.supports(cp)


def test_other_vehicle_untouched():
  cp = stock_cp(CAR.HONDA_CIVIC_BOSCH)
  before = cp.to_dict()
  assert apply_profile(cp, [eps()], Settings()) == "baseline"
  assert cp.to_dict() == before


def test_effective_base_gains_and_90_percent_multipliers_are_separate():
  imported = {"kpBP": [0.0], "kpV": [0.04], "kiBP": [0.0], "kiV": [0.013], "kf": 0.000014}
  cp = candidate_cp(InsightBasePid=imported, LatPScaleLowSpeed=90, LatPScaleStandard=90)
  assert cp.lateralTuning.pid.kpV[0] == pytest.approx(0.04)
  _, snapshot = decode(cp)
  assert snapshot["LatPScaleLowSpeed"] == snapshot["LatPScaleStandard"] == 90
  assert snapshot["NrdrLatStiction"] is False


@pytest.mark.parametrize("angle", [-500, -250, -40, -3, 0, 3, 40, 250, 500])
def test_firmware_geometry_round_trip(angle):
  profile = HONDA_VGR_PROFILES[0]
  assert profile.linear_to_physical(profile.physical_to_linear(angle)) == pytest.approx(angle, abs=1e-8)


@pytest.mark.parametrize("mode", [0, 1, 3])
@pytest.mark.parametrize("angle", [-180, -10, 0, 10, 180])
def test_measured_and_desired_curvature_share_geometry(mode, angle):
  cp = candidate_cp(NrdrSteerRatioMode=mode)
  lac, vm = controller(cp), VehicleModel(cp)
  cs, lp = inputs()
  cs.steeringAngleDeg = angle
  curvature = lac.measured_curvature(cs, vm, lp, active=True)
  desired = lac.geometry.desired_angle(vm, cs, lp, curvature) + lp.angleOffsetDeg
  assert desired == pytest.approx(angle, abs=1e-7)


def test_learned_geometry_is_frozen_during_engagement():
  cp = candidate_cp(NrdrSteerRatioMode=1)
  lac, vm = controller(cp), VehicleModel(cp)
  cs, lp = inputs()
  lac.measured_curvature(cs, vm, lp, active=True)
  assert vm.sR == 15.0
  lp.steerRatio = 20.0
  lac.measured_curvature(cs, vm, lp, active=True)
  assert vm.sR == 15.0
  lac.measured_curvature(cs, vm, lp, active=False)
  lac.measured_curvature(cs, vm, lp, active=False)
  assert vm.sR == 20.0


def test_raw_clarity_geometry_unavailable_on_insight():
  cp = candidate_cp(NrdrSteerRatioMode=2)
  geometry = InsightGeometry(cp, decode(cp)[1])
  assert "No audited raw-angle curve" in geometry.unavailable_reason
  assert not geometry.firmware_selected


@pytest.mark.parametrize("stiction", [False, True])
def test_saturation_override_and_disengage_reset(stiction):
  cp = candidate_cp(NrdrLatStiction=stiction, LatPScaleStandard=500, HondaCenterScale=5)
  lac, vm = controller(cp), VehicleModel(cp)
  cs, lp = inputs()
  lac.measured_curvature(cs, vm, lp, True)
  def step(active=True, curvature=0.2):
    return lac.update(active, cs, vm, lp, False, curvature, False, 0.1, None, None, SimpleNamespace())
  for _ in range(100):
    output, _, _ = step()
    assert math.isfinite(output) and abs(output) <= 1
  cs.steeringPressed = True
  assert step()[0] == 0
  assert lac.pid.i == 0
  cs.steeringPressed = False
  step()
  assert step(False)[0] == 0
  assert lac.pid.i == lac.previous_output == 0
  for bad in (float("nan"), float("inf"), -float("inf")):
    assert step(curvature=bad)[0] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1e9, 1e9, None])
def test_malformed_settings_remain_finite_and_bounded(value):
  cp = candidate_cp(LatPScaleStandard=value, HondaLpfTauStandard=value)
  values = decode(cp)[1]
  assert 0 <= values["LatPScaleStandard"] <= 500
  assert 0 <= values["HondaLpfTauStandard"] <= 1


@pytest.mark.parametrize("smoothing,limiter", [(False, False), (True, False), (False, True), (True, True)])
def test_filter_honors_takeover_before_any_smoothing(smoothing, limiter):
  cp = candidate_cp(HondaTorqueLowPassFilter=smoothing, HondaSteerDeltaLimiter=limiter)
  filt = InsightTorqueFilter(cp, 0.01)
  values = [filt.update(1.0, 15.0, True, False) for _ in range(100)]
  assert all(0 <= value <= 1 for value in values)
  if limiter:
    assert np.max(np.diff([0] + values)) <= 0.030001
  assert filt.update(1.0, 15.0, True, True) == 0
  assert filt.output == filt.filtered == 0
  filt.update(1.0, 15.0, True, False)
  assert filt.update(1.0, 15.0, False, False) == 0


def test_settings_snapshot_is_identical_across_processes_and_survives_serialization():
  cp = candidate_cp(LatPScaleLowSpeed=90, HondaLpfTauStandard=0.05)
  with car.CarParams.from_bytes(cp.to_bytes()) as restored:
    lac = controller(restored)
    filt = InsightTorqueFilter(restored, 0.01)
    assert dict(lac.settings) == dict(filt.settings)
    assert dict(lac.settings) == json.loads(restored.insightTuning)["settings"]
    with pytest.raises(TypeError):
      lac.settings["LatPScaleLowSpeed"] = 99


def test_eps_software_version_ignores_separate_serial_response():
  cp = stock_cp()
  assert apply_profile(cp, [eps(), eps(b"\x0c SERIAL-RESPONSE")], Settings()) == "selected"
  assert InsightLatControlPID.supports(cp)


def test_effective_tune_report_round_trips_schema():
  import json
  from cereal import log
  cp = candidate_cp(LatPScaleLowSpeed=90, NrdrLatStiction=False)
  lac = controller(cp)
  message = log.ControlsState.new_message()
  message.insightSteering = lac.diagnostic_report
  message.insightSteerRatio = 16.82
  with log.ControlsState.from_bytes(message.to_bytes()) as restored:
    report = json.loads(restored.insightSteering)
    assert report['controller'] == 'InsightLatControlPID'
    assert report['pScale'][0] == 90
    assert not report['stiction']
    assert restored.insightSteerRatio == pytest.approx(16.82)


@pytest.mark.parametrize('enabled', [False, True])
def test_controlsd_selects_actual_adapter_and_nnff_cannot_replace_pid(monkeypatch, enabled):
  from cereal import custom
  from openpilot.selfdrive.controls import controlsd
  from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
  cp = candidate_cp() if enabled else stock_cp()
  encoded = {'CarParams': cp.to_bytes(), 'StarPilotCarParams': custom.StarPilotCarParams.new_message().to_bytes()}
  monkeypatch.setattr(controlsd, 'Params', lambda: SimpleNamespace(get=lambda key, **kwargs: encoded.get(key)))
  subscriber = SimpleNamespace(extend=lambda services: subscriber)
  monkeypatch.setattr(controlsd.messaging, 'SubMaster', lambda *args, **kwargs: subscriber)
  monkeypatch.setattr(controlsd.messaging, 'PubMaster', lambda *args, **kwargs: SimpleNamespace())
  monkeypatch.setattr(controlsd, 'get_starpilot_toggles', lambda: SimpleNamespace(nnff=True, nnff_lite=True))
  controls = controlsd.Controls()
  assert type(controls.LaC) is (InsightLatControlPID if enabled else LatControlPID)


@pytest.mark.parametrize('signal', ['vEgo', 'steeringAngleDeg'])
def test_invalid_measurement_never_publishes_nonfinite_curvature(signal):
  cp = candidate_cp(NrdrSteerRatioMode=0)
  lac, vm = controller(cp), VehicleModel(cp)
  cs, lp = inputs()
  setattr(cs, signal, float('nan'))
  assert lac.measured_curvature(cs, vm, lp, active=True) == 0.
