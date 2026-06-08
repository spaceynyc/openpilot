#!/usr/bin/env python3
from __future__ import annotations

from openpilot.common.constants import CV


VISION_LEAD_APPROACH_MIN_MODEL_PROB = 0.70
VISION_LEAD_APPROACH_MIN_CLOSING_SPEED = 0.75
VISION_LEAD_APPROACH_TRIGGER_TIME = 3.2
VISION_LEAD_APPROACH_FULL_TIME = 1.2
VISION_LEAD_APPROACH_MIN_DECEL = 0.25
VISION_LEAD_APPROACH_MAX_DECEL = 1.15

RADARLESS_MATCHED_FOLLOW_MIN_SPEED = 22.0 * CV.MPH_TO_MS
RADARLESS_MATCHED_FOLLOW_MAX_REL_SPEED = 2.0
RADARLESS_MATCHED_FOLLOW_MIN_HEADWAY = 0.95
RADARLESS_MATCHED_FOLLOW_HEADWAY_BELOW_TARGET = 0.35
RADARLESS_MATCHED_FOLLOW_HEADWAY_ABOVE_TARGET = 0.90
RADARLESS_MATCHED_FOLLOW_MAX_LEAD_BRAKE = 0.35
RADARLESS_MATCHED_FOLLOW_MIN_MODEL_PROB = 0.70


def is_radarless_matched_follow_window(v_ego: float, lead_distance: float, v_lead: float, t_follow: float, *,
                                       radar: bool = False, lead_brake: float = 0.0,
                                       lead_prob: float = 0.0) -> bool:
  if radar or float(t_follow) <= 0.0 or float(v_ego) < RADARLESS_MATCHED_FOLLOW_MIN_SPEED:
    return False
  if float(lead_prob) < RADARLESS_MATCHED_FOLLOW_MIN_MODEL_PROB:
    return False
  if float(lead_brake) > RADARLESS_MATCHED_FOLLOW_MAX_LEAD_BRAKE:
    return False

  relative_speed = float(v_ego) - float(v_lead)
  if abs(relative_speed) > RADARLESS_MATCHED_FOLLOW_MAX_REL_SPEED:
    return False

  actual_headway = float(lead_distance) / max(float(v_ego), 1e-3)
  min_headway = max(RADARLESS_MATCHED_FOLLOW_MIN_HEADWAY,
                    float(t_follow) - RADARLESS_MATCHED_FOLLOW_HEADWAY_BELOW_TARGET)
  max_headway = float(t_follow) + RADARLESS_MATCHED_FOLLOW_HEADWAY_ABOVE_TARGET
  return min_headway <= actual_headway <= max_headway


def get_vision_lead_approach_cap(lead, v_ego: float, accel_min: float, desired_gap: float) -> float | None:
  if lead is None or not lead.status or bool(getattr(lead, "radar", False)):
    return None

  lead_prob = float(getattr(lead, "modelProb", 0.0))
  if lead_prob < VISION_LEAD_APPROACH_MIN_MODEL_PROB:
    return None

  lead_brake = max(0.0, -float(getattr(lead, "aLeadK", 0.0)))
  closing_speed = max(0.0, float(v_ego) - float(lead.vLead))
  projected_closing_speed = closing_speed + 0.5 * lead_brake
  if projected_closing_speed < VISION_LEAD_APPROACH_MIN_CLOSING_SPEED:
    return None

  gap_to_target = float(lead.dRel) - float(desired_gap)
  time_to_target = gap_to_target / max(projected_closing_speed, 0.1)
  if time_to_target > VISION_LEAD_APPROACH_TRIGGER_TIME:
    return None

  time_factor = (VISION_LEAD_APPROACH_TRIGGER_TIME - time_to_target) / (
    VISION_LEAD_APPROACH_TRIGGER_TIME - VISION_LEAD_APPROACH_FULL_TIME
  )
  prob_factor = (lead_prob - VISION_LEAD_APPROACH_MIN_MODEL_PROB) / (1.0 - VISION_LEAD_APPROACH_MIN_MODEL_PROB)
  approach_decel = VISION_LEAD_APPROACH_MAX_DECEL * max(0.0, min(time_factor, 1.0))
  approach_decel *= 0.65 + 0.35 * max(0.0, min(prob_factor, 1.0))
  approach_decel = max(approach_decel, 0.7 * lead_brake)

  if approach_decel < VISION_LEAD_APPROACH_MIN_DECEL:
    return None

  return max(float(accel_min), -approach_decel)


def get_matched_follow_brake_floor(lead, v_ego: float, t_follow: float, desired_gap: float) -> float | None:
  if lead is None or not lead.status:
    return None

  if not is_radarless_matched_follow_window(
    v_ego,
    lead.dRel,
    lead.vLead,
    t_follow,
    radar=bool(getattr(lead, "radar", False)),
    lead_brake=max(0.0, -float(getattr(lead, "aLeadK", 0.0))),
    lead_prob=float(getattr(lead, "modelProb", 0.0)),
  ):
    return None

  actual_headway = float(lead.dRel) / max(float(v_ego), 1e-3)
  target_headway = float(desired_gap) / max(float(v_ego), 1e-3)
  if actual_headway >= target_headway:
    return None

  # When we are basically matching the lead, avoid one extra hard dip.
  return -0.25
