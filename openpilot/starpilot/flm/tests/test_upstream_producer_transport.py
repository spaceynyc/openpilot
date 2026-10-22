"""Installed source closure only; no kernel or model-output equivalence proof."""
import hashlib
from pathlib import Path

import pytest

from openpilot.starpilot.flm import producer_clock_equivalence as clock


APPROVED = {
  'selfdrive/modeld/modeld.py': '43ef68eaef76540b562a64b1f4dda4f1c155de1476f3caf3500279db2202e588',
  'visionipc/visionipc_server.cc': 'a4325a5171eacb931eb271d06a992d2f27216040df113d92099f62cdfa67d81a',
  'visionipc/visionipc_client.cc': 'd142cd60eedabfc581e9770fcd6f0ca81b762ba9dbc608b54dd20a27a317a52d',
}


def test_exact_installed_upstream_closure_is_checked_before_contract():
  contract = clock.pinned_default_contract()
  assert len(clock.PRODUCER_SOURCE_HASHES) == 19
  assert {relative: expected for _, relative, expected in clock.PRODUCER_SOURCE_HASHES if relative in APPROVED} == APPROVED
  assert contract.producer_revision == clock.CLOSURE_SHA256
  assert contract.proof_sha256 == clock.CLOSURE_SHA256


@pytest.mark.parametrize('relative', tuple(APPROVED))
def test_unapproved_change_to_each_new_producer_fails_closed(monkeypatch, relative):
  original = Path.read_bytes
  reached = []
  def changed(path):
    body = original(path)
    if path.as_posix().endswith(relative):
      reached.append(path)
      assert hashlib.sha256(body).hexdigest() == APPROVED[relative]
      return body + b'\n'
    return body
  monkeypatch.setattr(Path, 'read_bytes', changed)
  with pytest.raises(ValueError, match='unsupported original mixed-producer source closure'):
    clock.pinned_default_contract()
  assert len(reached) == 1


def test_unapproved_second_producer_cannot_be_blessed_by_new_modeld_pin(monkeypatch):
  rows = list(clock.PRODUCER_SOURCE_HASHES)
  index = next(index for index, (_, relative, _) in enumerate(rows) if relative == 'system/sensord/sensord.py')
  package, relative, _ = rows[index]
  rows[index] = (package, relative, '0' * 64)
  monkeypatch.setattr(clock, 'PRODUCER_SOURCE_HASHES', tuple(rows))
  with pytest.raises(ValueError, match='unsupported original mixed-producer source closure'):
    clock.pinned_default_contract()
