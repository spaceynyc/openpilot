import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "mici" / "layouts" / "settings" / "network" / "action_state.py"
SPEC = importlib.util.spec_from_file_location("wifi_ui_action_state_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
should_show_forget_button = MODULE.should_show_forget_button


class TestWifiUI(unittest.TestCase):
  def test_should_show_forget_button_for_connected_network_without_saved_flag(self):
    network = SimpleNamespace(is_saved=False, is_connected=True)

    self.assertTrue(should_show_forget_button(network))

  def test_should_show_forget_button_for_saved_network(self):
    network = SimpleNamespace(is_saved=True, is_connected=False)

    self.assertTrue(should_show_forget_button(network))

  def test_should_hide_forget_button_for_unsaved_disconnected_network(self):
    network = SimpleNamespace(is_saved=False, is_connected=False)

    self.assertFalse(should_show_forget_button(network))
