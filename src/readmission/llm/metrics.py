"""Per-field precision and recall of extracted risk factors against the labelled notes.

Scalar fields are scored as slot filling. A value is positive when it says something: a condition
other than `other`, a disposition other than `not_stated`, a true flag. A positive prediction
equal to the label is a true positive, a positive prediction that differs is a false positive,
and a positive label the prediction does not equal is a false negative (so a wrong condition
counts once each way).

List fields (`comorbidities`, `social_risk_factors`) are scored per term, micro-averaged over
notes. Terms match when equal after `extract.normalize_term` (lowercase, spaces and hyphens to
underscores, synonyms to the vocabulary term); evidence spans are not compared. A note the model
gave no valid answer for counts as predicting nothing, so it costs recall, not precision.
"""

from dataclasses import dataclass

from readmission.llm.extract import normalize_term

SCALAR_FIELDS = {
    "primary_condition": "other",
    "prior_admissions_mentioned": False,
    "discharge_disposition": "not_stated",
    "follow_up_planned": False,
}
LIST_FIELDS = {"comorbidities": "term", "social_risk_factors": "factor"}
FIELDS = [
    "primary_condition",
    "comorbidities",
    "prior_admissions_mentioned",
    "discharge_disposition",
    "social_risk_factors",
    "follow_up_planned",
]


@dataclass(frozen=True)
class FieldScore:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None:
            return None
        return 2 * p * r / (p + r) if p + r else 0.0


def flatten(answer: dict | None) -> dict:
    """A validated answer in label form: scalar values and sorted, normalized term lists. No
    answer is the all-negative label."""
    if answer is None:
        return {**SCALAR_FIELDS, **{k: [] for k in LIST_FIELDS}}
    flat = {k: answer[k]["value"] for k in SCALAR_FIELDS}
    for key, name in LIST_FIELDS.items():
        flat[key] = sorted({normalize_term(item[name]) for item in answer[key]})
    return flat


def field_counts(predicted: dict, label: dict) -> dict[str, tuple[int, int, int]]:
    """(tp, fp, fn) per field for one note; both arguments are in label form."""
    counts = {}
    for key, negative in SCALAR_FIELDS.items():
        p, g = predicted[key], label[key]
        tp = int(p != negative and p == g)
        fp = int(p != negative and p != g)
        fn = int(g != negative and p != g)
        counts[key] = (tp, fp, fn)
    for key in LIST_FIELDS:
        p = {normalize_term(t) for t in predicted[key]}
        g = {normalize_term(t) for t in label[key]}
        counts[key] = (len(p & g), len(p - g), len(g - p))
    return counts


def score(pairs: list[tuple[dict, dict]]) -> dict[str, FieldScore]:
    """Micro-averaged scores per field over (predicted, label) pairs in label form."""
    totals = {k: [0, 0, 0] for k in FIELDS}
    for predicted, label in pairs:
        for key, counts in field_counts(predicted, label).items():
            totals[key] = [a + b for a, b in zip(totals[key], counts, strict=True)]
    return {k: FieldScore(*totals[k]) for k in FIELDS}


def mean_f1(scores: dict[str, FieldScore]) -> float:
    """Mean F1 over the fields; a field with no positives in labels or predictions scores 0, so a
    field cannot drop out of the mean by being empty."""
    return sum(s.f1 or 0.0 for s in scores.values()) / len(scores)
