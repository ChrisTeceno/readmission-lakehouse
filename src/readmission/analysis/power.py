"""Simulation-based power for the follow-up call pilot (docs/pilot-design.md).

A design is a schedule: which sequences receive the intervention in which periods. Clusters
(Contoso's facilities) are randomized to sequences within size strata. Each simulated trial
draws facility and facility-period effects on the probability scale, draws readmissions as
binomial counts per facility and period, and runs the analysis the pilot commits to: a
discharge-weighted regression of the facility-period readmission rate on the intervention with
period effects (and facility effects when the design compares facilities with themselves),
tested by permuting the randomization. Power is the share of trials whose permutation p-value is
at or below alpha.

The ICC is on the proportion scale: the between-facility variance of the true 30-day
readmission rate over p(1 - p). The cluster autocorrelation (CAC) is the share of that variance
that is stable over the trial, so a facility-period effect has variance ICC (1 - CAC) p (1 - p)
(Hooper, Teerenstra, de Hoop and Eldridge 2016).
"""

from dataclasses import dataclass

import numpy as np

POWER_TARGET = 0.8
ALPHA = 0.05


@dataclass(frozen=True)
class Design:
    name: str
    # Sequences x periods, 1 where the sequence has the intervention.
    schedule: np.ndarray
    # Facility fixed effects in the analysis. Needs a period without the intervention in every
    # sequence that later has it, or the effect is not identified within facilities.
    within_facility: bool

    @property
    def sequences(self) -> int:
        return self.schedule.shape[0]

    @property
    def periods(self) -> int:
        return self.schedule.shape[1]


def stepped_wedge(sequences: int) -> Design:
    """One baseline period with no facility on the intervention, then one sequence switches on
    at the start of each later period and stays on."""
    periods = sequences + 1
    schedule = (np.arange(periods)[None, :] > np.arange(sequences)[:, None]).astype(float)
    return Design(f"stepped wedge, {sequences} steps", schedule, within_facility=True)


def parallel_with_baseline(periods: int) -> Design:
    """Half the facilities switch on after a baseline period; the rest stay on usual care."""
    schedule = np.zeros((2, periods))
    schedule[1, 1:] = 1.0
    return Design("parallel, baseline period", schedule, within_facility=True)


def parallel_post_only() -> Design:
    """Half the facilities on the intervention for the whole trial, compared across facilities."""
    return Design("parallel, no baseline", np.array([[0.0], [1.0]]), within_facility=False)


def size_strata(volumes: np.ndarray, stratum_size: int) -> list[np.ndarray]:
    """Facility indexes grouped by volume, largest first, `stratum_size` to a group."""
    order = np.argsort(-np.asarray(volumes), kind="stable")
    if len(order) % stratum_size:
        raise ValueError(f"{len(order)} facilities do not split into strata of {stratum_size}")
    return [order[i : i + stratum_size] for i in range(0, len(order), stratum_size)]


def allocate(
    strata: list[np.ndarray], sequences: int, draws: int, rng: np.random.Generator
) -> np.ndarray:
    """`draws` random allocations, draws x facilities, each facility's sequence. Every stratum
    spreads its facilities evenly over the sequences, so each sequence gets one large, one
    medium and one small facility when the strata are sized to the number of sequences."""
    clusters = sum(len(s) for s in strata)
    out = np.empty((draws, clusters), dtype=int)
    for stratum in strata:
        if len(stratum) % sequences:
            raise ValueError(f"a stratum of {len(stratum)} does not split over {sequences}")
        slots = np.tile(np.arange(sequences), len(stratum) // sequences)
        out[:, stratum] = rng.permuted(np.broadcast_to(slots, (draws, len(slots))), axis=1)
    return out


def treatment(design: Design, allocations: np.ndarray) -> np.ndarray:
    """Intervention indicator per allocation, flattened facility-major to facilities x periods."""
    return design.schedule[allocations].reshape(allocations.shape[0], -1)


def residual_operator(design: Design, sizes: np.ndarray) -> np.ndarray:
    """G = W M, with W the discharge weights and M the weighted residual maker for the period
    (and facility) effects. By Frisch-Waugh-Lovell the weighted least squares estimate of the
    intervention effect is x'Gy / x'Gx, so one matrix serves every allocation."""
    clusters, periods = sizes.shape
    cells = clusters * periods
    columns = [np.kron(np.ones((clusters, 1)), np.eye(periods))]
    if design.within_facility:
        columns.append(np.kron(np.eye(clusters), np.ones((periods, 1))))
    d = np.hstack(columns)
    w = np.diag(sizes.reshape(cells).astype(float))
    return w - w @ d @ np.linalg.pinv(d.T @ w @ d) @ d.T @ w


def effect_estimates(g: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Weighted least squares intervention effect for each allocation in `x` (a x cells) and
    each trial in `y` (r x cells): an a x r array."""
    return (x @ g @ y.T) / np.einsum("ij,jk,ik->i", x, g, x)[:, None]


@dataclass(frozen=True)
class PowerResult:
    effects: np.ndarray
    power: np.ndarray
    mean_estimate: np.ndarray


def simulate_power(
    design: Design,
    sizes: np.ndarray,
    baseline: np.ndarray,
    effects: np.ndarray,
    icc: float,
    cac: float,
    strata: list[np.ndarray],
    reps: int,
    permutations: int,
    seed: int,
    alpha: float = ALPHA,
) -> PowerResult:
    """Power of the permutation test for each absolute reduction in `effects`.

    `sizes` is facilities x periods (discharges per facility-period, already in design
    periods) and `baseline` the facilities' usual-care readmission rates. Every effect reuses
    the same allocations and facility effects, and the binomial draws restart from the same
    seed, so the power curve is smooth in the effect (common random numbers).
    """
    clusters, periods = sizes.shape
    if periods != design.periods:
        raise ValueError(f"sizes have {periods} periods, the design has {design.periods}")
    rng = np.random.default_rng([seed, 0])
    g = residual_operator(design, sizes)
    reference = treatment(design, allocate(strata, design.sequences, permutations, rng))
    actual = treatment(design, allocate(strata, design.sequences, reps, rng))

    p_bar = float(np.average(baseline, weights=sizes.sum(axis=1)))
    variance = icc * p_bar * (1 - p_bar)
    stable = rng.normal(0, np.sqrt(variance * cac), (reps, clusters, 1))
    shock = rng.normal(0, np.sqrt(variance * (1 - cac)), (reps, clusters, periods))
    usual = (baseline[None, :, None] + stable + shock).reshape(reps, -1)
    n = np.broadcast_to(sizes.reshape(-1).astype(np.int64), usual.shape)

    power, mean_estimate = [], []
    for effect in effects:
        draws = np.random.default_rng([seed, 1])
        rate = np.clip(usual - effect * actual, 1e-6, 1 - 1e-6)
        y = draws.binomial(n, rate) / sizes.reshape(-1)
        observed = np.einsum("rj,jk,rk->r", actual, g, y) / np.einsum(
            "rj,jk,rk->r", actual, g, actual
        )
        null = effect_estimates(g, reference, y)
        extreme = (np.abs(null) >= np.abs(observed)[None, :] - 1e-12).sum(axis=0)
        p_value = (1 + extreme) / (1 + permutations)
        power.append(float(np.mean(p_value <= alpha)))
        mean_estimate.append(float(np.mean(observed)))
    return PowerResult(np.asarray(effects, float), np.array(power), np.array(mean_estimate))


def minimum_detectable_effect(
    effects: np.ndarray, power: np.ndarray, target: float = POWER_TARGET
) -> float:
    """Smallest effect whose power reaches `target`, interpolating linearly between grid points;
    nan when the grid never reaches it."""
    above = np.nonzero(np.asarray(power) >= target)[0]
    if not len(above):
        return float("nan")
    i = above[0]
    if i == 0:
        return float(effects[0])
    e0, e1, p0, p1 = effects[i - 1], effects[i], power[i - 1], power[i]
    return float(e0 + (target - p0) * (e1 - e0) / (p1 - p0))


def anova_icc(events: np.ndarray, sizes: np.ndarray) -> float:
    """One-way ANOVA estimator of the ICC for a binary outcome with unequal cluster sizes
    (Donner 1986): (MSB - MSW) / (MSB + (n0 - 1) MSW). It goes negative when hospitals vary
    less than sampling alone would make them."""
    events = np.asarray(events, float)
    sizes = np.asarray(sizes, float)
    k, total = len(sizes), sizes.sum()
    p = events / sizes
    p_bar = events.sum() / total
    msb = np.sum(sizes * (p - p_bar) ** 2) / (k - 1)
    msw = np.sum(sizes * p * (1 - p)) / (total - k)
    n0 = (total - np.sum(sizes**2) / total) / (k - 1)
    return float((msb - msw) / (msb + (n0 - 1) * msw))


def bootstrap_icc(
    events: np.ndarray, sizes: np.ndarray, draws: int, seed: int
) -> tuple[float, float]:
    """95 percent percentile interval for `anova_icc`, resampling hospitals."""
    rng = np.random.default_rng(seed)
    events = np.asarray(events, float)
    sizes = np.asarray(sizes, float)
    picks = rng.integers(0, len(sizes), (draws, len(sizes)))
    values = [anova_icc(events[i], sizes[i]) for i in picks]
    low, high = np.percentile(values, [2.5, 97.5])
    return float(low), float(high)


def between_variance(events: np.ndarray, sizes: np.ndarray) -> float:
    """Variance of hospitals' true rates: the spread of observed rates less the average
    binomial sampling variance, p (1 - p) / (n - 1) being its unbiased estimate."""
    p = np.asarray(events, float) / np.asarray(sizes, float)
    return float(np.var(p, ddof=1) - np.mean(p * (1 - p) / (np.asarray(sizes) - 1)))


def window_correlation(
    events_a: np.ndarray, sizes_a: np.ndarray, events_b: np.ndarray, sizes_b: np.ndarray
) -> float:
    """Correlation of hospitals' true rates between two performance windows that share no
    discharges. Sampling errors are independent across such windows, so the covariance of
    observed rates estimates the covariance of true rates; dividing by the between-hospital
    standard deviations of each window removes the attenuation sampling noise would cause."""
    pa = np.asarray(events_a, float) / np.asarray(sizes_a, float)
    pb = np.asarray(events_b, float) / np.asarray(sizes_b, float)
    covariance = np.cov(pa, pb, ddof=1)[0, 1]
    return float(
        covariance
        / np.sqrt(between_variance(events_a, sizes_a) * between_variance(events_b, sizes_b))
    )
