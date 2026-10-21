from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from readmission.analysis import power as pw
from readmission.ingest import contoso

EXTRACT = Path(__file__).parents[1] / "analysis" / "data"


def test_stepped_wedge_switches_one_sequence_on_per_period():
    design = pw.stepped_wedge(3)
    assert design.schedule.tolist() == [
        [0, 1, 1, 1],
        [0, 0, 1, 1],
        [0, 0, 0, 1],
    ]
    assert design.within_facility


def test_allocations_spread_every_stratum_over_every_sequence():
    volumes = np.arange(12, 0, -1) * 10.0
    strata = pw.size_strata(volumes, 4)
    assert [s.tolist() for s in strata] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]]
    allocations = pw.allocate(strata, 4, 500, np.random.default_rng(1))
    for stratum in strata:
        assert (np.sort(allocations[:, stratum], axis=1) == np.arange(4)).all()
    assert len({tuple(a) for a in allocations}) > 400


def test_effect_estimate_is_weighted_least_squares_with_period_and_facility_effects():
    rng = np.random.default_rng(2)
    design = pw.stepped_wedge(4)
    sizes = rng.integers(20, 300, (12, design.periods)).astype(float)
    x = pw.treatment(design, pw.allocate(pw.size_strata(sizes.sum(1), 4), 4, 1, rng))
    y = rng.uniform(0.1, 0.3, (1, x.shape[1]))
    estimate = pw.effect_estimates(pw.residual_operator(design, sizes), x, y)[0, 0]

    periods = np.tile(np.eye(design.periods), (12, 1))
    facilities = np.kron(np.eye(12), np.ones((design.periods, 1)))[:, 1:]
    regressors = np.hstack([x.T, periods, facilities])
    root_w = np.sqrt(sizes.reshape(-1))[:, None]
    coefficients = np.linalg.lstsq(regressors * root_w, y.T * root_w, rcond=None)[0]
    assert estimate == pytest.approx(coefficients[0, 0])


def test_simulated_power_matches_the_analytic_answer_for_a_parallel_trial():
    """80 equal clusters of 200, half on the intervention, compared across clusters: the
    difference in cluster means is normal with a known variance, so power has a closed form."""
    clusters, m, p0, effect, icc = 80, 200, 0.2, 0.035, 0.02
    between = icc * p0 * (1 - p0)
    p1 = p0 - effect
    se = np.sqrt(
        (between + p0 * (1 - p0) / m) / (clusters / 2)
        + (between + p1 * (1 - p1) / m) / (clusters / 2)
    )
    z = NormalDist()
    analytic = z.cdf(effect / se - z.inv_cdf(1 - pw.ALPHA / 2))

    design = pw.parallel_post_only()
    sizes = np.full((clusters, 1), m)
    strata = pw.size_strata(np.ones(clusters), 2)
    result = pw.simulate_power(
        design, sizes, np.full(clusters, p0), [effect], icc, 1.0, strata, 2000, 499, seed=3
    )
    assert analytic == pytest.approx(0.70, abs=0.01)
    assert result.power[0] == pytest.approx(analytic, abs=0.04)
    assert result.mean_estimate[0] == pytest.approx(-effect, abs=0.002)


def test_permutation_test_holds_its_size_in_a_twelve_facility_stepped_wedge():
    rng = np.random.default_rng(4)
    design = pw.stepped_wedge(4)
    sizes = rng.integers(30, 400, (12, 1)) * np.ones((1, design.periods))
    strata = pw.size_strata(sizes[:, 0], 4)
    baseline = rng.uniform(0.15, 0.22, 12)
    result = pw.simulate_power(
        design, sizes, baseline, [0.0, 0.05], 0.02, 0.5, strata, 2000, 199, seed=5
    )
    assert result.power[0] == pytest.approx(pw.ALPHA, abs=0.015)
    assert result.power[1] > 0.5


def test_simulate_power_rejects_sizes_that_do_not_match_the_design():
    with pytest.raises(ValueError, match="periods"):
        pw.simulate_power(
            pw.stepped_wedge(4),
            np.ones((12, 3)),
            np.full(12, 0.2),
            [0.0],
            0.01,
            0.8,
            pw.size_strata(np.ones(12), 4),
            10,
            9,
            seed=1,
        )


def test_minimum_detectable_effect_interpolates_to_the_target_power():
    effects = np.array([0.0, 0.01, 0.02, 0.03])
    assert pw.minimum_detectable_effect(effects, np.array([0.05, 0.4, 0.7, 0.9])) == (
        pytest.approx(0.025)
    )
    assert np.isnan(pw.minimum_detectable_effect(effects, np.array([0.05, 0.1, 0.2, 0.3])))


def test_anova_icc_is_negative_without_between_cluster_variation():
    events = np.full(10, 20)
    sizes = np.full(10, 100)
    assert pw.anova_icc(events, sizes) == pytest.approx(-1 / 99)


def test_anova_icc_and_window_correlation_recover_simulated_values():
    rng = np.random.default_rng(6)
    hospitals, m = 3000, 400
    sd = np.sqrt(0.02 * 0.2 * 0.8)
    stable = rng.normal(0, sd * np.sqrt(0.7), hospitals)
    early = 0.2 + stable + rng.normal(0, sd * np.sqrt(0.3), hospitals)
    late = 0.2 + stable + rng.normal(0, sd * np.sqrt(0.3), hospitals)
    sizes = np.full(hospitals, m)
    events_early = rng.binomial(sizes, early)
    events_late = rng.binomial(sizes, late)
    assert pw.anova_icc(events_early, sizes) == pytest.approx(0.02, abs=0.004)
    assert pw.window_correlation(events_early, sizes, events_late, sizes) == pytest.approx(
        0.7, abs=0.06
    )
    low, high = pw.bootstrap_icc(events_early, sizes, 200, seed=7)
    assert low < 0.02 < high


def test_contoso_extract_matches_the_seed_generators():
    """The committed extract came from Silver; the seed is deterministic per month, so the same
    12 months regenerated here must give the same volumes and calls."""
    extract = pd.read_csv(EXTRACT / "pilot_contoso_operations.csv", dtype={"facility_id": str})
    first = date.fromisoformat(extract["first_month"].min())
    months = [contoso.add_months(first, i) for i in range(12)]
    discharges = [row for month in months for row in contoso.discharge_rows(month)]
    calls = pd.DataFrame(
        contoso.followup_rows(discharges),
        columns=["contoso_facility_id", "month", "measure", "within_72h", "attempted", "completed"],
    )
    expected = (
        pd.DataFrame(discharges, columns=["contoso_facility_id", "month", "measure", "discharges"])
        .groupby(["contoso_facility_id", "measure"])["discharges"]
        .sum()
        .to_frame()
        .join(calls.groupby(["contoso_facility_id", "measure"])["completed"].sum())
        .join(
            calls[calls["within_72h"]]
            .groupby(["contoso_facility_id", "measure"])["completed"]
            .sum()
            .rename("within_72h")
        )
    )
    actual = extract.set_index(["contoso_facility_id", "measure"]).sort_index()
    expected = expected.sort_index()
    assert actual.index.equals(expected.index)
    assert (actual["discharges"] == expected["discharges"]).all()
    assert (actual["calls_completed"] == expected["completed"]).all()
    assert (actual["calls_completed_within_72h"] == expected["within_72h"]).all()
    ccns = {f.facility_id: f.ccn for f in contoso.FACILITIES}
    assert (extract["facility_id"] == extract["contoso_facility_id"].map(ccns)).all()
