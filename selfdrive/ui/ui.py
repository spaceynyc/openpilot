#!/usr/bin/env python3
import os

from openpilot.system.hardware import TICI
from openpilot.common.realtime import config_realtime_process, set_core_affinity
from openpilot.common.watchdog import kick_watchdog
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.stall_monitor import UIStallMonitor
from openpilot.selfdrive.ui.ui_state import ui_state

BIG_UI = gui_app.big_ui()


def main():
  cores = {5, }
  config_realtime_process(0, 51)

  stall_monitor = UIStallMonitor("raylib_ui")
  stall_monitor.progress("ui.before_init_window")
  stall_monitor.start()

  try:
    gui_app.init_window("UI")
    stall_monitor.progress("ui.after_init_window")
    gui_app.set_progress_hook(stall_monitor.progress)
    kick_watchdog()
    stall_monitor.progress("ui.before_layout_init")
    if BIG_UI:
      from openpilot.selfdrive.ui.layouts.main import MainLayout
      MainLayout()
    else:
      from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
      MiciMainLayout()
    stall_monitor.progress("ui.after_layout_init")
    kick_watchdog()
    stall_monitor.progress("ui.loop_ready")

    for should_render in gui_app.render():
      stall_monitor.progress("ui.loop_iteration")
      kick_watchdog()
      stall_monitor.progress("ui.after_watchdog")
      ui_state.update()
      stall_monitor.progress("ui.after_state_update")
      if should_render:
        # reaffine after power save offlines our core
        if TICI and os.sched_getaffinity(0) != cores:
          try:
            set_core_affinity(list(cores))
          except OSError:
            pass
      stall_monitor.progress("ui.loop_idle")
  finally:
    gui_app.set_progress_hook(None)
    stall_monitor.stop()


if __name__ == "__main__":
  main()
