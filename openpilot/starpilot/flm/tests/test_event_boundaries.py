"""Whole-event independence cannot be fabricated by interrupted neutral periods."""
from types import SimpleNamespace

from openpilot.starpilot.flm.event_windows import whole_event_windows
from openpilot.starpilot.flm.event_episodes import tracking_episodes

BASE = 1_000_000_000


def trajectory(final_neutral):
  values = ([.4] * 70 + [0.] * 40 + [.4] * 50 + [0.] * final_neutral)
  frames = [SimpleNamespace(control_ns=BASE + index * 10_000_000, desired_physical_accel=value,
    inputs=(('vEgo', 30.),)) for index, value in enumerate(values)]
  return SimpleNamespace(records=frames, replay_windows=((frames[0].control_ns, frames[-1].control_ns),),
    _frame_context=lambda _: True, identity='a' * 64, baseline_revision='b' * 64)


def test_interrupted_neutral_timer_does_not_split_one_large_maneuver():
  unfinished = trajectory(11)
  assert whole_event_windows(unfinished) == ()
  finished = trajectory(51)
  events = whole_event_windows(finished)
  assert len(events) == 1 and events[0][1] == BASE
  assert events[0][2] == finished.records[-1].control_ns


def test_tracking_attempt_count_uses_same_contiguous_neutral_recovery():
  objective = SimpleNamespace(group='ff.left', names=('ff_gain_left',), anchor_digest='c' * 64, context_key='d' * 64)
  model = SimpleNamespace(heldout_rmse=.001)
  for neutral, expected in ((11, 0), (51, 1)):
    engine = trajectory(neutral)
    samples = tuple({'control_ns': frame.control_ns,
      'control': {'stage_values': (('setpoint', frame.desired_physical_accel),)},
      'physical': {'lateral_accel': -.2}} for frame in engine.records[::5])
    blocks = ({'block_id': 'whole-physical-turn', 'samples': samples},)
    episodes = tracking_episodes(engine, blocks, model, objective)
    assert len(episodes) == expected
    if episodes:
      assert episodes[0]['block_ids'] == ('whole-physical-turn',)
