"""Synthetic transport; actual HKG parser/Card/SD state/Controls/CI/native chain.

Root loads its explicitly built current native library before collecting this file.
GV70 configuration is inferred source coverage, not the unknown reported CP.
"""
import os
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.radar_interface import RadarInterface
from opendbc.car.hyundai.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.starpilot.aol.intent import AOL_TOGGLE, read_settings
from openpilot.starpilot.aol.runtime import current_native
from openpilot.starpilot.aol.tests.test_canfd_stock_profiles import params
try:
  import _hkg24_callers as caller_fixture
except ModuleNotFoundError as error:
  if error.name != '_hkg24_callers':
    raise
  from openpilot.starpilot.aol.tests import test_ioniq_availability as caller_fixture
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
from opendbc.safety.tests.libsafety import libsafety_py


def parser_frames(ci, tick, *, main=0, lkas=0, button=0, engaged=False):
  frames = []
  for parser in ci.can_parsers.values():
    packer = CANPacker(parser.dbc.name)
    for address in sorted(parser.addresses):
      message = parser.dbc.addr_to_msg[address]
      name = message.name
      # Physical button source is50Hz even where the host tolerates long gaps.
      physical_hz = {'CRUISE_BUTTONS': 50, 'CRUISE_BUTTONS_ALT': 50, 'TCS': 50, 'SCC_CONTROL': 50,
                     'DOORS_SEATBELTS': 10, 'BLINKERS': 10, 'FR_CMR_02_100ms': 10,
                     'CAM_0x2a4': 20, 'CAM_0x362': 20}.get(name)
      if physical_hz is None:
        declared = parser.message_states[address].frequency
        physical_hz = min(declared, 100) if 0 < declared <= 100 else 100
      period = max(1, round(100 / physical_hz))
      if tick % period:
        continue
      values = {}
      if name == ci.CS.cruise_btns_msg_canfd:
        values.update(COUNTER=tick % (16 if name == 'CRUISE_BUTTONS' else 256),
                      ADAPTIVE_CRUISE_MAIN_BTN=main, LDA_BTN=lkas, CRUISE_BUTTONS=button)
      elif name == 'SCC_CONTROL':
        values.update(MainMode_ACC=1, ACCMode=int(engaged), VSetDis=90)
      elif name == ci.CS.gear_msg_canfd:
        values['GEAR'] = 5
      elif name == 'DOORS_SEATBELTS':
        values['DRIVER_SEATBELT'] = 1
      elif name == 'MDPS':
        values['MDPS_StrTqSnsrVal'] = 0
      elif name == 'WHEEL_SPEEDS':
        values.update(WHL_SpdFLVal=40, WHL_SpdFRVal=40, WHL_SpdRLVal=40, WHL_SpdRRVal=40)
      elif name == 'STEERING_SENSORS':
        values.update(STEERING_ANGLE=1., STEERING_RATE=0.)
      elif name == 'TCS':
        values.update(ACCEnable=0, ACC_REQ=int(engaged), DriverBraking=0)
      if 'COUNTER' in message.sigs:
        values['COUNTER'] = (tick // period) % (1 << message.sigs['COUNTER'].size)
      frames.append(packer.make_can_msg(name, parser.bus, values))
  return frames


def _invalid_controls_emits_no_commands(received):
  if 'sendcan' not in received:
    return True
  envelope = received['sendcan']
  return envelope.which() == 'sendcan' and not envelope.valid and len(envelope.sendcan) == 0


def _exercise_join(identity, alternate, incidental, withdrawal, monkeypatch, *, shared_long=False):
  native = libsafety_py.libsafety
  case = caller_fixture.IoniqAvailabilityTests(methodName='runTest')
  card = None
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', True, block=True)
    saved.put('LKASButtonControl', AOL_TOGGLE, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    now = [10_000_000_000]
    holder = None
    query = None
    for key in ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'):
      saved.put(key, 0, block=True)
    try:
      cp = params(identity, alternate=alternate)
      if shared_long:
        from types import SimpleNamespace
        from opendbc.car.hyundai import ecu_startup
        from opendbc.car.hyundai.ecu_startup import Outcome
        from opendbc.car.hyundai.torque_ev_startup import TorqueEVStartup
        from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
        saved.put_bool('AlphaLongitudinalEnabled', True, block=True)
        saved.put_bool('IsReleaseBranch', False, block=True)
        holder = VehicleStartupOwner()
        sent = []
        def query(sender, receiver, bus, addresses, requests, responses):
          assert bus == 1 and addresses == [(0x730, None)]
          request = requests[0]
          assert request in (b'\x10\x03', b'\x28\x83\x01', b'\x28\x00\x01')
          def receive(timeout):
            sender([(0x730, bytes([len(request)]) + request + bytes(7-len(request)), bus)])
            reply = {b'\x10\x03': b'\x50\x03', b'\x28\x00\x01': b'\x68\x00'}.get(request)
            return {} if reply is None else {(0x730, None): reply}
          return SimpleNamespace(get_data=receive)
        with patch.object(ecu_startup, 'IsoTpParallelQuery', side_effect=query), \
             patch.object(card_module.time, 'clock_gettime_ns', side_effect=lambda clock: now[0]), \
             patch('opendbc.car.hyundai.torque_ev_startup.time.sleep'):
          selected = holder.prepare(cp, CarInterface, (list, sent.extend), requested=True, admission=lambda: True)
          assert isinstance(holder.owner, TorqueEVStartup)
          assert holder.owner.outcome is Outcome.SENT_UNCONFIRMED
          ci = CarInterface(selected)
          card = card_module.Car(CI=ci, RI=RadarInterface(ci.CP), startup_owner=holder.owner)
        assert holder.owner.ready and holder.owner.published and holder.owner.prepared_for(card.CP)
        assert holder.owner.disable_attempted and sent
      else:
        card = card_module.Car(CI=CarInterface(cp), RI=RadarInterface(cp))
      assert not card.ci_initialized and not saved.get_bool('ControlsReady')
      card.slc_producer_session = 'authority-card'
      card.CI.update([])
      sd, controls, drive = case._authority_callers(False, cp=card.CP.as_reader().as_builder(), genuine_events=True)
      # The reusable helper defaults this to None; exercise actual saved mappings.
      sd.aol_settings = read_settings(saved)
      assert sd.aol_settings.lkas_action == AOL_TOGGLE and sd.aol_settings.main_action == 0
      assert sd.CP.to_dict() == card.CP.to_dict() == controls.CP.to_dict()
      assert len(card.CP.safetyConfigs) == 1 and bool(card.CP.openpilotLongitudinalControl) == shared_long
      config = card.CP.safetyConfigs[0]
      model, word = int(config.safetyModel.raw), int(config.safetyParam)
      native.set_alternative_experience(card.CP.alternativeExperience)  # Admission readsAE during hook selection.
      assert native.set_safety_hooks(model, word) == 0
      native.init_tests()
      native.set_alternative_experience(card.CP.alternativeExperience)
      queued, received, previous_sd = [], {}, {}
      tick = 0
      patch_scope = monkeypatch.context()
      local = patch_scope.__enter__()
      try:
        local.setattr(card_module, 'REPLAY', False)
        local.setattr(card_module.time, 'monotonic_ns', lambda: now[0])
        local.setattr(card_module.time, 'clock_gettime_ns', lambda clock: now[0])
        local.setattr(card_module.messaging, 'drain_sock_raw', lambda *args, **kwargs: list(queued))
        local.setattr(card.sm, 'update', lambda *args: None)
        create_message = messaging.new_message
        def clocked_message(service=None, size=None, **kwargs):
          # Card publishes before drive() advances its helper clock this cycle.
          kwargs.setdefault('logMonoTime', now[0])
          return create_message(service, size, **kwargs)
        local.setattr(messaging, 'new_message', clocked_message)
        send = card.pm.send
        def capture(service, event):
          raw = event if isinstance(event, bytes) else event.to_bytes()
          received[service] = messaging.log_from_bytes(raw).as_builder()
          return send(service, raw)  # Serialize once; retain builder for drive().
        local.setattr(card.pm, 'send', capture)

        def step(*, main=0, lkas=0, button=0, engaged=False, withdraw_request=False):
          nonlocal tick
          tick += 1
          now[0] = source_ns = 10_000_000_000 + tick * 10_000_000
          prior_axis = previous_sd.get('aolAxisState')
          prior_state = previous_sd.get('selfdriveState')
          request = 0 if prior_axis is None else (
            int(prior_axis.aolAxisState.desiredLateral) | int(prior_axis.aolAxisState.desiredLongitudinal) << 1)
          if withdraw_request:
            request = 0  # Explicit transport-withdrawal negative, never a fabricated grant.
          heartbeat = bool(request or (prior_state is not None and prior_state.selfdriveState.enabled))
          native.set_timer(source_ns // 1000)
          native.set_aol_test_heartbeat(heartbeat)
          native.aol_set_host_request(request)
          frames = parser_frames(card.CI, tick, main=main, lkas=lkas, button=button, engaged=engaged)
          for address, data, bus in frames:
            native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
          if tick % 100 == 0:
            native.safety_tick()  # Actual native maintenance cadence is1Hz.
          actual_request = native.aol_get_request_mask()
          mask = native.aol_get_permission_mask()
          # Existing test export returns the COMMON core flag unconditionally;
          # despite its historical Toyota test name, this is a read-only native
          # observation, not a setter or a fabricated healthy Panda report.
          rx_invalid = bool(native.safety_test_toyota_rx_summary_invalid())
          relay_fault = bool(native.get_relay_malfunction())
          assert not relay_fault
          batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
          batch.logMonoTime = source_ns
          queued[:] = [batch.to_bytes()]
          device = messaging.new_message('deviceState', valid=True, logMonoTime=source_ns)
          device.deviceState.started, device.deviceState.startedMonoTime = True, 9_000_000_000
          pandas = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=source_ns)
          panda = pandas.pandaStates[0]
          panda.safetyModel, panda.safetyParam = config.safetyModel, word
          panda.alternativeExperience = card.CP.alternativeExperience
          panda.controlsAllowed = native.get_controls_allowed()
          panda.safetyRxChecksInvalid = rx_invalid
          panda.ignitionLine = True
          slot = PandaSlot(0, 'authority-panda', model, word, card.CP.alternativeExperience,
                           native.get_controls_allowed(), safetyRxChecksInvalid=rx_invalid)
          wire = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=source_ns)
          wire.aolSafetyWire = encode_safety(SafetyState(1, True, source_ns, source_ns + 30_000_000,
            model, word, bool(mask & 1), bool(mask & 2), bool(actual_request & 1), bool(actual_request & 2),
            'authority-panda', card.aol_axis_session(), source_ns, (slot,)))
          messages = [device.as_reader(), pandas.as_reader(), wire.as_reader()]
          messages += [previous_sd[name] for name in ('selfdriveState', 'aolAxisState', 'onroadEvents') if name in previous_sd]
          if 'onroadEvents' not in previous_sd:
            messages.append(messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=source_ns).as_reader())
          card.sm.update_msgs(source_ns / 1e9, messages)
          state, _ = card.state_update()
          if state.canValid:
            assert state.cruiseState.available  # Assert parsed value BEFORE helper touches it.
          for service in ('carState', 'carOutput', 'aolIntentWire', 'starpilotCarState'):
            received.pop(service, None)
          card.state_publish(state, None)
          state_envelope = received['carState']
          output_envelope = received['carOutput']
          assert state_envelope.logMonoTime == source_ns and state_envelope.valid == state.canValid
          assert output_envelope.logMonoTime == source_ns
          # Exact captured envelopes, including validity and published body.
          axis, host, command = drive(tick, intent_message=received['aolIntentWire'],
            car_state_message=state_envelope, car_output_message=output_envelope,
            companion=False, authority_message=received.get('starpilotCarState'),
            allowed=bool(mask & 1), long_allowed=bool(mask & 2), native_request_lat=bool(actual_request & 1),
            native_request_long=bool(actual_request & 2), panda_controls_allowed=native.get_controls_allowed(),
            panda_fault=rx_invalid, relay_fault=relay_fault)
          if sd.conditional_car_state_valid:
            assert sd.aol_car_state_log_ns == state_envelope.logMonoTime
          assert controls.sm.logMonoTime['carState'] == state_envelope.logMonoTime
          assert controls.sm.valid['carState'] == state_envelope.valid
          assert controls.sm['carState'].to_dict() == state_envelope.carState.to_dict()
          assert controls.sm.logMonoTime['carOutput'] == output_envelope.logMonoTime
          assert controls.sm.valid['carOutput'] == output_envelope.valid
          assert controls.sm['carOutput'].to_dict() == output_envelope.carOutput.to_dict()
          previous_sd.update(sd.pm.messages)
          validated = current_native(sd.sm, sd.CP, now_ns=source_ns, axis_session_id=sd.aol_session_id)
          if not rx_invalid:
            assert validated is not None and validated.lateralAllowed == bool(mask & 1)
            assert validated.requestedLateral == bool(actual_request & 1)
          else:
            assert not axis.lateralActive and not axis.longitudinalActive
            assert not command.latActive and not command.longActive
          # Forward exact Controls publication at a later receipt; source unchanged.
          now[0] += 1
          card.sm.update_msgs(now[0] / 1e9, [controls.pm.messages['carControl']])
          received.pop('sendcan', None)
          card.controls_update(state, card.sm['carControl'])
          steering = []
          if state.canValid:
            assert 'sendcan' in received and received['sendcan'].valid
            steering = [frame for frame in received['sendcan'].sendcan
                        if frame.address == (0x110 if alternate else 0x50) and frame.src == card.CI.CC.CAN.ACAN]
            assert steering
            for frame in received['sendcan'].sendcan:
              assert native.safety_tx_hook(libsafety_py.make_CANPacket(frame.address, frame.src, frame.dat))
          return state, axis, host, command, steering

        for _ in range(120):
          step()
        assert not card.aol_card_intent.allowed_latch
        assert native.safety_config_valid()  # All selected physical RX sources are actually primed.
        for _ in range(2):
          step(lkas=1)  # Span a complete50Hz physical sample.
        for _ in range(15):
          state, axis, host, command, _ = step()
        assert state.canValid and axis.lateralActive and command.latActive
        assert not host.enabled and not command.longActive
        for _ in range(2):
          step(button=2)  # Actual SET press/release; no controlsAllowed setter.
        for _ in range(20):
          state, axis, host, command, _ = step(engaged=True)
        assert state.cruiseState.enabled and host.enabled and native.get_controls_allowed()
        assert axis.lateralActive and command.latActive
        if shared_long:
          assert axis.longitudinalActive and command.longActive
        # Full engagement is a legitimate driver-owned intent epoch. Anchor
        # after its real publication, immediately before incidental buttons.
        edge = card.aol_card_intent._last_latch_edge_ns
        for _ in range(2):
          state, axis, host, command, _ = step(main=int(incidental == 'main'), button=4 if incidental == 'cancel' else 0)
          assert card.aol_card_intent.allowed_latch, 'HKG_INDEPENDENT_LATCH_DROPPED'
          assert native.aol_get_permission_mask() & 1, 'HKG_NATIVE_LATERAL_DROPPED'
          assert axis.lateralActive and command.latActive, 'HKG_JOINED_LATERAL_DROPPED'
          assert not axis.longitudinalActive and not command.longActive and not host.enabled
        nonzero = False
        for _ in range(20):
          state, axis, host, command, steering = step()
          assert card.aol_card_intent.allowed_latch, 'HKG_INDEPENDENT_LATCH_DROPPED'
          assert card.aol_card_intent._last_latch_edge_ns == edge, 'HKG_INCIDENTAL_EDGE_RENEWED'
          assert native.aol_get_permission_mask() & 1, 'HKG_NATIVE_LATERAL_DROPPED'
          assert axis.lateralActive and command.latActive, 'HKG_JOINED_LATERAL_DROPPED'
          assert not state.cruiseState.enabled and not host.enabled and not native.get_controls_allowed()
          assert not axis.longitudinalActive and not command.longActive
          nonzero |= abs(command.actuators.torque) > 0
          assert steering
        if shared_long:
          # MAIN has no ordinary LONG-cancel meaning in this owner. Exercise it
          # after real CANCEL; never fabricate a MAIN-driven LONG transition.
          for _ in range(2):
            state, axis, host, command, _ = step(main=1)
          assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
          assert not host.enabled and not command.longActive and not native.get_controls_allowed()
          for _ in range(20):
            state, axis, host, command, _ = step()
            assert card.aol_card_intent.allowed_latch and axis.lateralActive and command.latActive
            assert card.aol_card_intent._last_latch_edge_ns == edge
            assert not host.enabled and not axis.longitudinalActive and not command.longActive
            assert not native.get_controls_allowed()
        assert nonzero, 'Actual Controls did not produce nonzero correction for measured steering error'
        assert card.ci_initialized and saved.get_bool('ControlsReady')
        invalid = controls.pm.messages['carControl'].as_builder()
        invalid.valid = False
        now[0] += 1
        card.sm.update_msgs(now[0] / 1e9, [invalid.as_reader()])
        received.pop('sendcan', None)
        card.controls_update(state, card.sm['carControl'])
        # Startup owners publish an invalid EMPTY envelope on rejected controls.
        # Even neutral frames are forbidden here; validity alone is insufficient.
        assert _invalid_controls_emits_no_commands(received), 'Invalid Controls packet produced valid or nonempty Card sendcan'
        assert _invalid_controls_emits_no_commands({})
        empty = messaging.new_message('sendcan', 0, valid=False)
        assert _invalid_controls_emits_no_commands({'sendcan': empty.as_reader()})
        for valid, frame_count in ((True, 0), (False, 1), (True, 1)):
          rejected = messaging.new_message('sendcan', frame_count, valid=valid)
          assert not _invalid_controls_emits_no_commands({'sendcan': rejected.as_reader()})
        if withdrawal == 'toggle-off':
          for _ in range(2):
            step(lkas=1)
        for _ in range(3):
          state, axis, host, command, _ = step(withdraw_request=withdrawal == 'native-request-off')
        assert not axis.lateralActive and not command.latActive
        assert not axis.longitudinalActive and not command.longActive and not host.enabled
        if withdrawal == 'toggle-off':
          assert not card.aol_card_intent.allowed_latch
        assert native.aol_get_permission_mask() == 0
      finally:
        patch_scope.__exit__(None, None, None)
    finally:
      native.init_tests()
      native.set_alternative_experience(0)
      native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      case.doCleanups()
      if card is not None:
        card.vehicle_startup.close()
      if holder is not None:
        # Even a failed constructor uses synthetic restoration transport.
        from opendbc.car.hyundai import ecu_startup
        with patch.object(ecu_startup, 'IsoTpParallelQuery', side_effect=query):
          holder.close()


@pytest.mark.parametrize('identity,alternate', [
  pytest.param(CAR.GENESIS_GV70_ELECTRIFIED_1ST_GEN, False, id='gv70-standard'),
  pytest.param(CAR.KIA_EV6, True, id='ev6-alternate')])
@pytest.mark.parametrize('incidental', ['cancel', 'main'])
@pytest.mark.parametrize('withdrawal', ['toggle-off', 'native-request-off'])
def test_mapped_aol_actual_join(identity, alternate, incidental, withdrawal, monkeypatch):
  _exercise_join(identity, alternate, incidental, withdrawal, monkeypatch)


def _native_variant():
  native = libsafety_py.libsafety
  try:
    # allOutput is registered only by ALLOW_DEBUG builds; probe the loaded library.
    status = native.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0)
    assert status in (0, -1)
  finally:
    native.init_tests()
    native.set_alternative_experience(0)
    assert native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0) == 0
  detected = 'debug' if status == 0 else 'release'
  expected = os.environ.get('HKG_NATIVE_VARIANT')
  assert expected in (None, 'debug', 'release'), 'Invalid HKG_NATIVE_VARIANT'
  assert expected is None or expected == detected, 'HKG_NATIVE_VARIANT disagrees with loaded safety library'
  return detected


@pytest.mark.parametrize('declared', [None, 'debug', 'release', 'invalid'])
def test_native_variant_checks_declaration_and_restores_no_output(declared, monkeypatch):
  monkeypatch.delenv('HKG_NATIVE_VARIANT', raising=False)
  actual = _native_variant()
  if declared is not None:
    monkeypatch.setenv('HKG_NATIVE_VARIANT', declared)
  if declared is None or declared == actual:
    assert _native_variant() == actual
  else:
    with pytest.raises(AssertionError, match='HKG_NATIVE_VARIANT'):
      _native_variant()
  native = libsafety_py.libsafety
  assert native.get_current_safety_mode() == structs.CarParams.SafetyModel.noOutput
  assert not native.get_controls_allowed()
  assert native.aol_get_request_mask() == native.aol_get_permission_mask() == 0


@pytest.mark.parametrize('withdrawal', ['toggle-off', 'native-request-off'])
def test_shared_long_actual_cancel_then_main_join(withdrawal, monkeypatch):
  if _native_variant() == 'release':
    _release_long_denial(alternate=withdrawal == 'native-request-off')
  else:
    _exercise_join(CAR.HYUNDAI_IONIQ_5, False, 'cancel', withdrawal, monkeypatch, shared_long=True)


def _release_long_denial(*, alternate):
  from opendbc.car.hyundai.torque_ev_startup import candidate
  cp = params(CAR.HYUNDAI_IONIQ_5, alternate=alternate, release=True)
  assert candidate(cp, requested=True, is_release=True) is None
  assert not cp.openpilotLongitudinalControl
  ci = CarInterface(cp)
  ci.update([])
  native = libsafety_py.libsafety
  try:
    # Unsupported hooks can return0 with an empty configuration. Prove actual
    # RX/request/permission/TX denial instead of interpreting that return code.
    native.set_alternative_experience(32)  # Supply validAE even for RELEASE denial.
    native.set_safety_hooks(structs.CarParams.SafetyModel.hyundaiCanfd, 0x895 if alternate else 0x815)
    native.init_tests()
    native.set_alternative_experience(32)
    native.set_aol_test_heartbeat(True)
    for tick in range(30):
      now = 10_000_000_000 + tick * 10_000_000
      native.set_timer(now // 1000)
      frames = parser_frames(ci, tick, lkas=int(14 <= tick <= 15), button=2 if tick == 20 else 0)
      for address, data, bus in frames:
        native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      state = ci.update([(now, frames)])
      native.aol_set_host_request(3)
      actual_request = native.aol_get_request_mask()
      permission = native.aol_get_permission_mask()
      assert actual_request == permission == 0 and not native.get_controls_allowed()
    assert state.canValid
    packer = CANPacker('hyundai_canfd_generated')
    commands: tuple[tuple[str, int, dict[str, float]], ...] = (
      ('LKAS_ALT' if alternate else 'LKAS', 0, {'ActToiSta': 1, 'StrTqReqVal': 1}),
      ('SCC_CONTROL', 1, {'ACCMode': 1, 'aReqRaw': .1, 'aReqValue': .1}))
    for name, bus, values in commands:
      address, data, owner_bus = packer.make_can_msg(name, bus, values)
      assert not native.safety_tx_hook(libsafety_py.make_CANPacket(address, owner_bus, data))
  finally:
    native.init_tests()
    native.set_alternative_experience(0)
    native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


def test_dedicated_ev6_long_uses_its_own_host_namespace():
  from openpilot.starpilot.tests.test_ev6_startup import TestEV6Startup
  from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
  from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent
  from openpilot.starpilot.car.hyundai.aol import independent_cruise_buttons_supported
  with OpenpilotPrefix(), patch.dict(os.environ, {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}):
    saved = Params()
    for key in ('AlwaysOnLateral', 'OpenpilotEnabledToggle', 'AlphaLongitudinalEnabled'):
      saved.put_bool(key, True, block=True)
    saved.put_bool('IsReleaseBranch', False, block=True)
    saved.put('LKASButtonControl', AOL_TOGGLE, block=True)
    saved.put('MainCruiseButtonControl', 0, block=True)
    # Existing fixture seals before returning; let actual Card own finalization
    # and sealing, matching its production startup order.
    with patch.object(VehicleStartupOwner, 'seal_publication'):
      ci, holder, _ = TestEV6Startup().exercise(mode='sent_restore')
    card = None
    try:
      card = card_module.Car(CI=ci, RI=RadarInterface(ci.CP), startup_owner=holder.owner)
      assert holder.owner.ready and holder.owner.published and holder.owner.prepared_for(card.CP)
      assert card.CP.openpilotLongitudinalControl and not card.CP.pcmCruise
      assert isinstance(card.aol_card_intent, Ev6CardIntent)
      assert not card.aol_card_intent.independent_cruise_buttons
      assert not independent_cruise_buttons_supported(card.CP, marked_only=True)
      assert not card.ci_initialized and not saved.get_bool('ControlsReady')
    finally:
      holder.close()
