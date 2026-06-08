import json
import platform

import numpy as np

from cereal import log, custom
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import AutoLaneChangeController, AutoLaneChangeMode
from openpilot.sunnypilot.selfdrive.controls.lib.lane_turn_desire import LaneTurnController

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
TurnDirection = custom.ModelDataV2SP.TurnDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
NAV_TURN_DISTANCE_SPEED_BREAKPOINTS = [0.0, 5.0, 10.0]
NAV_TURN_DISTANCE_BREAKPOINTS = [20.0, 25.0, 30.0]
NAV_KEEP_DISTANCE_SPEED_BREAKPOINTS = [0.0, 15.0, 30.0]
NAV_KEEP_DISTANCE_BREAKPOINTS = [25.0, 90.0, 160.0]

DESIRES = {
  LaneChangeDirection.none: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.none,
    LaneChangeState.laneChangeFinishing: log.Desire.none,
  },
  LaneChangeDirection.left: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.laneChangeLeft,
    LaneChangeState.laneChangeFinishing: log.Desire.laneChangeLeft,
  },
  LaneChangeDirection.right: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.laneChangeRight,
    LaneChangeState.laneChangeFinishing: log.Desire.laneChangeRight,
  },
}

TURN_DESIRES = {
  TurnDirection.none: log.Desire.none,
  TurnDirection.turnLeft: log.Desire.turnLeft,
  TurnDirection.turnRight: log.Desire.turnRight,
}


class DesireHelper:
  def __init__(self):
    self.params = Params()
    self.params_memory = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.keep_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none
    self.alc = AutoLaneChangeController(self)
    self.lane_turn_controller = LaneTurnController(self)
    self.lane_turn_direction = TurnDirection.none
    self.nav_desires_allowed = False
    self._nav_param_counter = -1
    self._nav_instruction_state_raw: object = None
    self._nav_instruction_state: dict[str, object] = {}

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def _update_nav_params(self):
    self._nav_param_counter += 1
    if self._nav_param_counter % 60 == 0:
      self.nav_desires_allowed = self.params.get_bool("NavDesiresAllowed")

    raw = self.params_memory.get("NavInstructionState") or {}
    if raw == self._nav_instruction_state_raw:
      return

    self._nav_instruction_state_raw = raw
    if not raw:
      self._nav_instruction_state = {}
      return

    if isinstance(raw, dict):
      self._nav_instruction_state = raw
      return

    if isinstance(raw, bytes):
      raw = raw.decode("utf-8", errors="ignore")

    if isinstance(raw, str):
      try:
        parsed = json.loads(raw)
        self._nav_instruction_state = parsed if isinstance(parsed, dict) else {}
        return
      except json.JSONDecodeError:
        pass

    self._nav_instruction_state = {}

  @staticmethod
  def _nav_keep_direction_is_clear(carstate, lane_change_direction):
    return not (
      (lane_change_direction == LaneChangeDirection.left and carstate.leftBlindspot) or
      (lane_change_direction == LaneChangeDirection.right and carstate.rightBlindspot)
    )

  @staticmethod
  def _nav_torque_applied(carstate, lane_change_direction):
    return carstate.steeringPressed and (
      (lane_change_direction == LaneChangeDirection.left and carstate.steeringTorque > 0) or
      (lane_change_direction == LaneChangeDirection.right and carstate.steeringTorque < 0)
    )

  @staticmethod
  def _nav_turn_is_imminent(carstate, maneuver_distance):
    try:
      distance = float(maneuver_distance)
    except (TypeError, ValueError):
      return False

    return distance <= float(np.interp(carstate.vEgo, NAV_TURN_DISTANCE_SPEED_BREAKPOINTS, NAV_TURN_DISTANCE_BREAKPOINTS))

  @staticmethod
  def _nav_keep_is_imminent(carstate, maneuver_distance):
    try:
      distance = float(maneuver_distance)
    except (TypeError, ValueError):
      return False

    return distance <= float(np.interp(carstate.vEgo, NAV_KEEP_DISTANCE_SPEED_BREAKPOINTS, NAV_KEEP_DISTANCE_BREAKPOINTS))

  @staticmethod
  def _nav_effective_modifier(nav_instruction_state, carstate, maneuver_distance):
    modifier = str(nav_instruction_state.get("maneuverModifier", ""))
    maneuver_type = str(nav_instruction_state.get("maneuverType", ""))
    active_lane_direction = str(nav_instruction_state.get("activeLaneDirection", ""))

    if modifier in ("left", "right") and maneuver_type in ("off ramp", "fork") and DesireHelper._nav_keep_is_imminent(carstate, maneuver_distance):
      if active_lane_direction in ("slightLeft", "left"):
        return "slightLeft"
      if active_lane_direction in ("slightRight", "right"):
        return "slightRight"

    return modifier

  def _navigation_desire(self, carstate, lateral_active):
    self._update_nav_params()
    if not self.nav_desires_allowed or not lateral_active or not bool(self._nav_instruction_state.get("valid", False)):
      return log.Desire.none

    maneuver_distance = self._nav_instruction_state.get("maneuverDistance", 0.0)
    modifier = self._nav_effective_modifier(self._nav_instruction_state, carstate, maneuver_distance)
    if modifier == "slightLeft":
      if not carstate.rightBlinker and self._nav_keep_direction_is_clear(carstate, LaneChangeDirection.left):
        if self._nav_torque_applied(carstate, LaneChangeDirection.left):
          return log.Desire.keepLeft
    elif modifier == "slightRight":
      if not carstate.leftBlinker and self._nav_keep_direction_is_clear(carstate, LaneChangeDirection.right):
        if self._nav_torque_applied(carstate, LaneChangeDirection.right):
          return log.Desire.keepRight
    elif modifier in ("left", "sharpLeft"):
      if not carstate.rightBlinker and not carstate.leftBlindspot and carstate.vEgo < LANE_CHANGE_SPEED_MIN and not carstate.standstill and self._nav_turn_is_imminent(carstate, maneuver_distance):
        return log.Desire.turnLeft
    elif modifier in ("right", "sharpRight"):
      if not carstate.leftBlinker and not carstate.rightBlindspot and carstate.vEgo < LANE_CHANGE_SPEED_MIN and not carstate.standstill and self._nav_turn_is_imminent(carstate, maneuver_distance):
        return log.Desire.turnRight

    return log.Desire.none

  def update(self, carstate, lateral_active, lane_change_prob):
    self.alc.update_params()
    self.lane_turn_controller.update_params()
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    # Lane turn controller update
    self.lane_turn_controller.update_lane_turn(blindspot_left=carstate.leftBlindspot, blindspot_right=carstate.rightBlindspot,
                                               left_blinker=carstate.leftBlinker, right_blinker=carstate.rightBlinker, v_ego=v_ego)
    self.lane_turn_direction = self.lane_turn_controller.get_turn_direction()

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX or self.alc.lane_change_set_timer == AutoLaneChangeMode.OFF:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
    else:
      # LaneChangeState.off
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_ll_prob = 1.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        self.alc.update_lane_change(blindspot_detected, carstate.brakePressed)

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
        elif (torque_applied or self.alc.auto_lane_change_allowed) and not blindspot_detected:
          self.lane_change_state = LaneChangeState.laneChangeStarting

      # LaneChangeState.laneChangeStarting
      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        # fade out over .5s
        self.lane_change_ll_prob = max(self.lane_change_ll_prob - 2 * DT_MDL * 0.65, 0.0)

        # 98% certainty
        if lane_change_prob < 0.02 and self.lane_change_ll_prob < 0.01:
          self.lane_change_state = LaneChangeState.laneChangeFinishing

      # LaneChangeState.laneChangeFinishing
      elif self.lane_change_state == LaneChangeState.laneChangeFinishing:
        # fade in laneline over 1s
        self.lane_change_ll_prob = min(self.lane_change_ll_prob + DT_MDL * 0.65, 1.0)

        if self.lane_change_ll_prob > 0.99:
          self.lane_change_direction = LaneChangeDirection.none
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
          else:
            self.lane_change_state = LaneChangeState.off

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    self.prev_one_blinker = one_blinker

    if self.lane_turn_direction != TurnDirection.none:
      self.desire = TURN_DESIRES[self.lane_turn_direction]
    else:
      self.desire = DESIRES[self.lane_change_direction][self.lane_change_state]

    # Send keep pulse once per second during LaneChangeStart.preLaneChange
    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.laneChangeStarting):
      self.keep_pulse_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      self.keep_pulse_timer += DT_MDL
      if self.keep_pulse_timer > 1.0:
        self.keep_pulse_timer = 0.0
      elif self.desire in (log.Desire.keepLeft, log.Desire.keepRight):
        self.desire = log.Desire.none

    self.alc.update_state()

    nav_desire = self._navigation_desire(carstate, lateral_active)
    if nav_desire != log.Desire.none and self.lane_change_state == LaneChangeState.off:
      self.desire = nav_desire
