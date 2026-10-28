from pathlib import Path

import pytest

from readmission.analysis import realism
from readmission.ingest.contoso import ALL_PAYER_FACTOR, FACILITIES

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def generated():
    return realism.generated()


def test_benchmark_roster_is_contoso_roster():
    assert set(realism.CCN_DISCHARGES_2023) == {f.ccn for f in FACILITIES}


def test_cost_report_title_xviii_is_fee_for_service():
    """Title XVIII on the cost report agrees with MedPAR's fee-for-service count within 8
    percent at every CCN, so the all-cause ratio's denominator is fee-for-service Medicare."""
    for ffs, title_xviii, total in realism.CCN_DISCHARGES_2023.values():
        assert abs(title_xviii / ffs - 1) < 0.08
        assert total > title_xviii


def test_all_payer_factor_matches_the_estimate_and_stays_below_the_all_cause_ratio():
    share = realism.ohio_ffs_share()
    assert 0.44 < share < 0.51
    factors = [realism.condition_all_payer_factor(c) for c in realism.MEDICARE_SHARE_2018]
    assert min(factors) < realism.network_all_payer_factor() < max(factors)
    # 3.2 rounds the condition-level estimate of 3.21 (2.0 until 2026-10-28).
    assert ALL_PAYER_FACTOR == pytest.approx(realism.network_all_payer_factor(), abs=0.05)
    # All conditions is an upper bound for the six HRRP conditions; one uniform factor sits below
    # the network's ratio but above the lowest hospital's (CH11 Oak, 2.70).
    network, low, high = realism.all_cause_ratio()
    assert low < ALL_PAYER_FACTOR < network <= high
    verdicts = {c.property: c.verdict for c in realism.checks()}
    assert verdicts["All-payer discharges per Medicare FFS discharge, HRRP conditions"] == (
        realism.MATCHES
    )


def test_winter_excess_follows_the_generator(generated):
    discharges, _ = generated
    # Pneumonia's amplitude 0.25 gives about 59 percent more in December to February than June to
    # August before noise; AMI has no seasonal term. Hip/knee and CABG have none either, but at
    # about 38 and 52 discharges a month across the network, noise alone moves the ratio by about
    # 8 percent per standard deviation.
    assert 0.5 < realism.winter_excess(discharges, "PN") < 0.7
    assert 0.1 < realism.winter_excess(discharges, "HF") < 0.3
    assert abs(realism.winter_excess(discharges, "AMI")) < 0.05
    assert abs(realism.winter_excess(discharges, "HIP_KNEE")) < 0.25
    assert abs(realism.winter_excess(discharges, "CABG")) < 0.25


def test_call_shares_are_nested(generated):
    shares = realism.call_shares(*generated)
    assert shares["completed_72h"] < shares["completed"] < shares["attempted"] < 1
    # The pilot design quotes 42.7 percent over its 12 months; the 36 months land close to it.
    assert shares["completed_72h"] == pytest.approx(0.427, abs=0.01)
    low, high = realism.CALL_REACH_72_HOURS
    assert 0.48 < low < 0.5 and 0.64 < high < 0.66


def test_every_check_has_a_verdict_and_cites_a_source_when_it_has_a_benchmark():
    verdicts = {
        realism.MATCHES,
        realism.PLAUSIBLE,
        realism.OFF,
        realism.BY_CONSTRUCTION,
        realism.NO_BENCHMARK,
    }
    for check in realism.checks():
        assert check.verdict in verdicts
        if check.verdict != realism.NO_BENCHMARK:
            assert check.source


def test_doc_table_is_current():
    """docs/synthetic-data.md carries the script's output; rerun it with --write if this fails."""
    text = (ROOT / "docs" / "synthetic-data.md").read_text()
    committed = text.split("<!-- realism-check:begin -->\n", 1)[1].split(
        "\n<!-- realism-check:end -->", 1
    )[0]
    assert committed == realism.markdown(realism.checks())
