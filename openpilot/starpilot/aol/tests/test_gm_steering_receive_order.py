"""Card's exact raw companion can precede the cached SubMaster projection."""
from contextlib import ExitStack
from unittest.mock import patch
import pytest
from opendbc.car.gm.tests.test_volt_sdgm_control import sdgm_params
from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.common.realtime import Ratekeeper
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.selfdrive.car.tests.publication_fixture import initialize_publication_sources
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.car.gm.steering_companion import SteeringCompanion


@pytest.mark.parametrize('c9', [False, True])
@pytest.mark.parametrize('full_op', [False, True])
@pytest.mark.parametrize('condition', ['healthy', 'sm_fault', 'sm_invalid', 'sm_wrong_source',
                                     'sm_expired', 'sm_future', 'missing_exact', 'raw_fault', 'raw_invalid'])
def test_exact_card_raw_companion_survives_older_cached_projection(c9, full_op, condition):
  from openpilot.selfdrive.car.card import Car
  from openpilot.starpilot.aol.tests.test_gm_source_deadline import bind_steering_receipts
  actual_recv, actual_optional = messaging.recv_one, messaging.recv_one_or_none
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with ExitStack() as cleanup, OpenpilotPrefix():
    cleanup.callback(case.doCleanups)
    messaging.reset_context()
    sd, _, step = case._authority_callers(full_op, cp=sdgm_params(alpha=True, brake_c9=c9))
    step = bind_steering_receipts(sd, step, cleanup)
    assert step(0)[0].lateralActive
    pm = messaging.PubMaster(['carState', 'carOutput', 'starpilotCarState'])
    sd.car_state_sock = messaging.sub_sock('carState', timeout=100)
    sd.steering_companion = SteeringCompanion(messaging.sub_sock('starpilotCarState', conflate=False))
    latest = messaging.sub_sock('starpilotCarState', conflate=True, timeout=100)
    card = Car.__new__(Car)
    card.CP, card.pm = sd.CP, pm
    initialize_publication_sources(card)
    authority = card.CI.CS.steering_authority
    card.sm = messaging.SubMaster(['carControl'], ignore_avg_freq=['carControl'])
    neutral = messaging.new_message('carControl', valid=True, logMonoTime=10_000_000_000)
    card.sm.update_msgs(10., [neutral.as_reader()])
    card.sm.frame = 1
    card.car_params_published = True
    card.last_actuators_output = car.CarControl.Actuators.new_message()
    card.slc_replay = card.curve_replay = card.aol_replay = False
    card.can_rcv_cum_timeout_counter = 0
    card.rk = Ratekeeper(100., print_delay_threshold=None)
    card.v_cruise_helper = VCruiseHelper(card.CP)
    source = 10_010_000_000
    now = source + 8_000_000
    older = source - (40_000_000 if condition == 'sm_expired' else 10_000_000)
    original_message = messaging.new_message

    def publish(stamp, *, fault=False, valid=True, enabled=True):
      authority.enabled, authority.latched = enabled, fault
      cs = fixture.car_state()
      cs.canValid = valid
      cs.cruiseState.available = True
      with patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=lambda *a, **kw:
                 original_message(*a, **dict(kw, logMonoTime=stamp))):
        card.state_publish(cs, None)

    publish(older, fault=condition == 'sm_fault', valid=condition != 'sm_invalid')
    assert actual_recv(sd.car_state_sock).logMonoTime == older
    cached = actual_recv(latest).as_builder()
    assert cached.logMonoTime == older
    if condition == 'sm_wrong_source':
      cached.starpilotCarState.sourceCarStateMonoTime -= 1
    if condition == 'sm_future':
      cached.logMonoTime = now + 1
      cached.starpilotCarState.sourceCarStateMonoTime = now + 1

    def receive_first(sock):
      publish(source, fault=condition == 'raw_fault', enabled=condition != 'missing_exact')
      first = actual_recv(sock)
      assert first.logMonoTime == source
      return first

    def receive_cached(_timeout):
      sd.sm.update_msgs(now / 1e9, [cached.as_reader()])

    def receive_raw(sock):
      if sock is sd.car_state_sock:
        return None
      message = actual_optional(sock)
      if message is not None and condition == 'raw_invalid' and message.logMonoTime == source:
        message = message.as_builder()
        message.valid = False
        return message.as_reader()
      return message

    sd.sm.update.side_effect = receive_cached
    with patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', side_effect=receive_first), \
         patch('openpilot.starpilot.car.gm.steering_companion.messaging.recv_one_or_none', side_effect=receive_raw), \
         patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now):
      axis, _, cc = step(1)
    healthy = condition == 'healthy'
    assert axis.sourceCarStateMonoTime == source
    assert bool(axis.lateralActive) == healthy and bool(cc.latActive) == healthy
    assert bool(cc.longActive) == (healthy and full_op)
    assert (log.OnroadEvent.EventName.controlsMismatch in sd.events.names) == (not healthy)
    if healthy:
      assert sd.sm.logMonoTime['starpilotCarState'] == older
      assert sd.steering_companion.samples[source][0] == source
      assert axis.validUntilMonoTime == axis.observedMonoTime + 30_000_000
