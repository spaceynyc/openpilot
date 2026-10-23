import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.tests.test_modified_eps import eps
from opendbc.car.honda.values import CAR
from openpilot.starpilot.lateral import honda_crawl


def params(fw):
  return CarInterface.get_params(CAR.HONDA_INSIGHT, gen_empty_fingerprint(), [eps(fw)], False, False, False)


def test_only_the_modified_insight():
  assert honda_crawl.eligible(params(b'39990-TXM,A040\x00\x00'))
  assert not honda_crawl.eligible(params(b'39990-TXM-A040\x00\x00'))
  civic = CarInterface.get_params(CAR.HONDA_CIVIC_BOSCH, gen_empty_fingerprint(), [eps(b'39990-TGG,A020\x00\x00')],
                                  False, False, False)
  assert not honda_crawl.eligible(civic)
  assert not honda_crawl.eligible(structs.CarParams(brand="toyota", carFingerprint="HONDA_INSIGHT"))


@pytest.mark.parametrize("v_ego,expected", [(0.0, 0.0), (0.3, 0.0), (0.9, 0.5), (1.5, 1.0), (10.0, 1.0)])
def test_authority_fades_out_toward_a_stop(v_ego, expected):
  assert honda_crawl.crawl_authority(v_ego) == pytest.approx(expected)


def test_target_is_smoothed_at_a_crawl_and_passed_through_from_4_ms():
  target = honda_crawl.CrawlTarget(0.01)
  assert target.update(0.0, 0.5) == 0.0
  # a 5 degree jump in the model's target at a crawl moves only ~2% of the way in one 10 ms frame
  assert target.update(5.0, 0.5) == pytest.approx(5.0 * 0.01 / 0.51)
  for _ in range(300):
    target.update(5.0, 0.5)
  assert target.update(5.0, 0.5) == pytest.approx(5.0, abs=0.02)  # 3 s = 6 time constants
  assert target.update(-3.0, 4.0) == -3.0
  target.reset()
  assert target.update(7.0, 0.5) == 7.0
