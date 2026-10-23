import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.hud_renderer import FONT_SIZES, KM_TO_MILE, SET_SPEED_PERSISTENCE
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached


__all__ = ("NrdrHudRenderer",)

# The MAX block's drop shadow is centered 81 px in from the left edge; the current speed mirrors it on the right.
SPEED_SHADOW_RADIUS = 162 // 2


class NrdrHudRenderer:
  def _set_speed_color(self, alpha: float) -> rl.Color:
    color = (0, 255, 70) if ui_state.sm['longitudinalPlanSP'].speedLimit.assist.active else (255, 255, 255)
    return rl.Color(*color, int(255 * 0.9 * alpha))

  def _draw_additional_speed(self, rect: rl.Rectangle) -> None:
    if not self._can_draw_top_icons:
      return
    self._draw_current_speed_max_style(rect)

    # the persistent small set speed, top left, whenever the MAX block is not showing there
    if self._engaged and 0 < rl.get_time() - self._set_speed_changed_time < SET_SPEED_PERSISTENCE:
      return
    if self.is_cruise_set and self._engaged:
      set_speed = self.set_speed if ui_state.is_metric else self.set_speed * KM_TO_MILE
      color = rl.Color(0, 255, 70, 255) if ui_state.sm['longitudinalPlanSP'].speedLimit.assist.active else rl.WHITE
      rl.draw_text_ex(self._font_display, str(round(set_speed)), rl.Vector2(int(rect.x + 16), int(rect.y + 18)), 54, 0, color)

  def _draw_current_speed_max_style(self, rect: rl.Rectangle) -> None:
    """The current speed, always shown, styled like the MAX set speed and mirrored to the top right."""
    cx = rect.x + rect.width - SPEED_SHADOW_RADIUS
    rl.draw_circle_gradient(rl.Vector2(cx, rect.y + SPEED_SHADOW_RADIUS), SPEED_SHADOW_RADIUS,
                            rl.Color(0, 0, 0, 255 // 2), rl.BLANK)
    color = rl.Color(255, 255, 255, int(255 * 0.9))

    speed_text = str(round(self.speed))
    size = FONT_SIZES.set_speed
    while size > 72 and measure_text_cached(self._font_display, speed_text, size).x > 128:
      size -= 1
    width = measure_text_cached(self._font_display, speed_text, size).x
    rl.draw_text_ex(self._font_display, speed_text, rl.Vector2(cx - width / 2, rect.y + 3 - 8 - 3 + 4), size, 0, color)

    unit = tr("KM/H") if ui_state.is_metric else tr("MPH")
    unit_width = measure_text_cached(self._font_semi_bold, unit, FONT_SIZES.max_speed).x
    rl.draw_text_ex(self._font_semi_bold, unit, rl.Vector2(cx - unit_width / 2, rect.y + FONT_SIZES.set_speed - 7 + 4),
                    FONT_SIZES.max_speed, 0, color)
