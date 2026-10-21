"""The 50-note evaluation sample: stratified by specialty, proportional allocation, fixed seed.

Proportional allocation makes the sample look like the population the `llm_extract` task runs
over, so the metrics estimate what the batch job produces. Each specialty gets its quota by the
largest remainder method (ties broken by specialty name), and the notes inside a specialty are
drawn with `random.Random(SEED)` from its note ids in ascending order, so the sample depends only
on the set of notes, not on the order a query returned them in.
"""

import math
import random
from collections import defaultdict

SEED = 20261021
SAMPLE_SIZE = 50


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Largest remainder quotas: floor of each specialty's share of `n`, then one more note each
    for the largest remainders until the quotas sum to `n`."""
    total = sum(sizes.values())
    shares = {k: n * size / total for k, size in sizes.items()}
    quotas = {k: math.floor(share) for k, share in shares.items()}
    by_remainder = sorted(sizes, key=lambda k: (-(shares[k] - quotas[k]), k))
    for k in by_remainder[: n - sum(quotas.values())]:
        quotas[k] += 1
    return {k: q for k, q in quotas.items() if q}


def stratified_sample(
    notes: list[tuple[int, str]], n: int = SAMPLE_SIZE, seed: int = SEED
) -> list[tuple[int, str]]:
    """`notes` holds (note_id, medical_specialty) pairs; returns the sample sorted by specialty
    then note id."""
    by_specialty: dict[str, list[int]] = defaultdict(list)
    for note_id, specialty in notes:
        by_specialty[specialty].append(note_id)
    quotas = allocate({k: len(v) for k, v in by_specialty.items()}, n)
    rng = random.Random(seed)
    sample = []
    for specialty in sorted(quotas):
        ids = sorted(by_specialty[specialty])
        sample += [(note_id, specialty) for note_id in sorted(rng.sample(ids, quotas[specialty]))]
    return sample
