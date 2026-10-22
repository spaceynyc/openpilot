"""Optional-child lifecycle only; no controller/native admission is mocked here."""
import pytest

from openpilot.starpilot.flm.live_tracking_actor import LiveTrackingActor


class Process:
  def __init__(self, closed=True, error=None):
    self.closed, self.error, self.calls = closed, error, []
    self.child = None

  def close(self, reason):
    self.calls.append(reason)
    if self.error is not None:
      raise self.error
    return self.closed


@pytest.mark.parametrize('first_closed', (False, True))
def test_shutdown_reaps_both_optional_workers(first_closed):
  bootstrap, probe = Process(first_closed), Process()
  actor = LiveTrackingActor(bootstrap, probe_process=probe)
  assert actor.close('shutdown') is first_closed
  assert bootstrap.calls == probe.calls == ['shutdown']


def test_unexpected_shutdown_failure_still_attempts_other_reaper():
  failure = RuntimeError('bootstrap lifecycle failed')
  bootstrap, probe = Process(error=failure), Process()
  actor = LiveTrackingActor(bootstrap, probe_process=probe)
  with pytest.raises(RuntimeError, match='bootstrap lifecycle failed'):
    actor.close('shutdown')
  assert bootstrap.calls == probe.calls == ['shutdown']


class PollProcess(Process):
  def __init__(self, *, reap_after=0):
    super().__init__()
    self.child = object() if reap_after else None
    self.reap_after = reap_after
    self.polls, self.submissions = [], []

  def poll(self, *, token, now_ns):
    self.polls.append((token, now_ns))
    if self.reap_after:
      self.reap_after -= 1
      if not self.reap_after:
        self.child = None
    return None

  def submit(self, snapshot, *, token, now_ns):
    self.submissions.append((snapshot, token, now_ns))
    return True


def test_changed_source_waits_for_actual_old_probe_reap_before_bootstrap(monkeypatch):
  from dataclasses import replace
  from types import SimpleNamespace
  from openpilot.starpilot.flm.tests.test_live_tracking_rollout import _optimizer
  from openpilot.starpilot.flm import live_tracking_bootstrap_producer
  old = _optimizer().binding
  new = replace(old, source_epoch='9'*64)
  bootstrap, probe = PollProcess(), PollProcess(reap_after=3)
  actor = LiveTrackingActor(bootstrap, probe_process=probe)
  actor.seed = old
  monkeypatch.setattr(actor, 'binding_for', lambda collector, source: new)
  collector = SimpleNamespace(ready=[None], original_source_capture=SimpleNamespace(epoch=1), live_tracking=None)
  monkeypatch.setattr(live_tracking_bootstrap_producer, 'response_snapshot', lambda *args: {'lifecycle_test_only': True})
  assert actor.tick(collector, object(), now_ns=10, current_native=True) is None
  assert probe.child is not None and not bootstrap.submissions
  assert actor.tick(collector, object(), now_ns=20, current_native=True) is None
  assert probe.child is None
  assert len(bootstrap.submissions) == 1
  assert all(token is None for token, _ in probe.polls)


def test_invalid_binding_continues_nonblocking_reaping_without_new_job(monkeypatch):
  from types import SimpleNamespace
  bootstrap, probe = PollProcess(), PollProcess(reap_after=4)
  actor = LiveTrackingActor(bootstrap, probe_process=probe)
  def invalid(collector, source):
    raise ValueError('current binding unavailable')
  monkeypatch.setattr(actor, 'binding_for', invalid)
  collector = SimpleNamespace(ready=[None])
  for now in (10, 20, 30):
    assert actor.tick(collector, object(), now_ns=now, current_native=True) is None
  assert probe.child is None and not bootstrap.submissions
  assert all(token is None for token, _ in bootstrap.polls+probe.polls)
  assert not bootstrap.calls and not probe.calls  # No blocking close in tick.



def test_probe_transport_splits_only_work_not_genuine_endpoint_support(monkeypatch):
  from openpilot.starpilot.flm import live_tracking_probe_process
  process = PollProcess()
  process.submit_probe = process.submit
  actor = LiveTrackingActor(probe_process=process)
  jobs = [({'endpoint': index}, index) for index in range(4)]
  actor.probe_queue = list(jobs)
  actor.probe_queue_key = ((None, 'a'*64, 'b'*64), 1, 'ff.left', ('ff_gain_left',))
  def bounded_batch(rows, *, queued_ns):
    if len(rows) > 2:
      raise ValueError('numerical batch byte bound')
    return {'rows': rows, 'queued_ns': queued_ns}
  monkeypatch.setattr(live_tracking_probe_process, 'make_probe_batch', bounded_batch)
  assert actor._submit_probe_queue('token', 100)
  assert actor.probe_queue == jobs[2:]
  assert len(process.submissions[0][0]['rows']) == 2
  assert actor._submit_probe_queue('token', 200)
  assert actor.probe_queue == [] and actor.probe_queue_key is None
  assert [row['endpoint'] for submission, _, _ in process.submissions for row in submission['rows']] == list(range(4))
