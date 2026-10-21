"""Risk factor extraction: the prompt, the `ai_query` call and the checks every answer passes.

The model sees one note and answers with JSON for `schema.json`. An answer counts as valid only
when it parses, matches the schema after term normalization, and every evidence span is a
verbatim quote of the note. The schema alone cannot check the quotes, so `validate_answer` does.
Everything here is plain Python or SQL text, so the job and the evaluation share it and it unit
tests without a model.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources

import jsonschema

from readmission.silver.governance import _quote

SCHEMA = json.loads(resources.files("readmission.llm").joinpath("schema.json").read_text())
SCHEMA_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

PROMPT_VERSION = "v1"

# Room for a long answer plus the reasoning gpt-oss writes before it. A reply that runs away is
# cut off here and fails validation as not JSON.
MAX_TOKENS = 4096

# An evidence span is a short quote, not a paragraph.
MAX_EVIDENCE_CHARS = 300

COMORBIDITY_TERMS = SCHEMA["properties"]["comorbidities"]["items"]["properties"]["term"]["enum"]

# Synonyms a model writes instead of the vocabulary term. Matching is on the normalized term
# (see `normalize_term`), so only spellings that differ after normalization need an entry.
TERM_SYNONYMS = {
    "htn": "hypertension",
    "high_blood_pressure": "hypertension",
    "diabetes_mellitus": "diabetes",
    "type_2_diabetes": "diabetes",
    "type_2_diabetes_mellitus": "diabetes",
    "type_1_diabetes": "diabetes",
    "dm": "diabetes",
    "chf": "heart_failure",
    "congestive_heart_failure": "heart_failure",
    "cardiomyopathy": "heart_failure",
    "cad": "coronary_artery_disease",
    "afib": "atrial_fibrillation",
    "a_fib": "atrial_fibrillation",
    "ckd": "chronic_kidney_disease",
    "esrd": "chronic_kidney_disease",
    "end_stage_renal_disease": "chronic_kidney_disease",
    "dyslipidemia": "hyperlipidemia",
    "hypercholesterolemia": "hyperlipidemia",
    "degenerative_joint_disease": "osteoarthritis",
    "reflux": "gerd",
    "gastroesophageal_reflux_disease": "gerd",
    "obstructive_sleep_apnea": "sleep_apnea",
    "osa": "sleep_apnea",
    "cva": "stroke",
    "pvd": "peripheral_vascular_disease",
    "peripheral_arterial_disease": "peripheral_vascular_disease",
    "malignancy": "cancer",
}

PROMPT = f"""You extract hospital readmission risk factors from one clinical note.
Answer with a single JSON object and nothing else, with exactly these keys:

primary_condition: {{"value": ..., "evidence": ...}}. The HRRP condition that is the main reason
  for this encounter: "AMI" (acute myocardial infarction), "HF" (heart failure), "PN"
  (pneumonia), "COPD" (COPD exacerbation), "CABG" (a coronary artery bypass graft procedure),
  "HIP_KNEE" (elective total hip or knee arthroplasty), otherwise "other". An acute coronary
  syndrome not documented as an infarction is "other".
comorbidities: a list of {{"term": ..., "evidence": ...}}. Chronic conditions the note documents
  the patient as having, including the primary condition. Leave out denied conditions, family
  history and possible or rule-out diagnoses. Use only these terms:
  {", ".join(COMORBIDITY_TERMS)}.
  ESRD is chronic_kidney_disease, dyslipidemia is hyperlipidemia, degenerative joint disease is
  osteoarthritis, reflux esophagitis is gerd, any malignancy (also in remission) is cancer.
prior_admissions_mentioned: {{"value": true/false, "evidence": ...}}. True only if the note says
  the patient was admitted to a hospital before this encounter. Emergency department visits,
  prior surgeries and later admissions do not count.
discharge_disposition: {{"value": ..., "evidence": ...}}. Where the patient went at the end of
  this encounter, only if stated: "home", "home_with_services", "skilled_nursing_facility",
  "rehab_facility", "hospice", "transferred", "left_against_medical_advice", "expired",
  otherwise "not_stated". "Discharged" without a destination, recovery room, ICU or an admission
  is "not_stated".
social_risk_factors: a list of {{"factor": ..., "evidence": ...}} with factor one of
  "lives_alone", "transportation" (lacks transportation to care), "medication_access" (cannot
  afford or obtain medications). Only when documented as present.
follow_up_planned: {{"value": true/false, "evidence": ...}}. True if the note plans or instructs a
  follow-up visit or contact with a clinician. "Return as needed" or "if worse" alone is false.

Every evidence is a short quote (under {MAX_EVIDENCE_CHARS} characters) copied exactly from the
note. Give evidence for "other" or "not_stated" or false as null. Every list item needs evidence.

NOTE:
"""

PROMPT_SHA256 = hashlib.sha256(PROMPT.encode()).hexdigest()

RETRY_SUFFIX = "\n\nYour previous answer was rejected: {reason}. Answer again with JSON only."


@dataclass(frozen=True)
class Checked:
    answer: dict | None
    reason: str | None
    # The reply parsed and matched the schema, whether or not its evidence passed.
    schema_valid: bool = False

    @property
    def valid(self) -> bool:
        return self.reason is None


def response_format() -> str:
    return json.dumps(
        {
            "type": "json_schema",
            "json_schema": {"name": "readmission_risk_factors", "schema": SCHEMA, "strict": True},
        }
    )


def ai_query_sql(
    endpoint: str, note_column: str, *, structured: bool, suffix_column: str | None = None
) -> str:
    """The `ai_query` expression for one note per row. It never fails the query: an endpoint
    error comes back in `errorMessage` and is checked like any other bad answer."""
    request = f"concat({_quote(PROMPT)}, {note_column}"
    request += f", {suffix_column})" if suffix_column else ")"
    args = [
        _quote(endpoint),
        request,
        f"modelParameters => named_struct('temperature', 0.0, 'max_tokens', {MAX_TOKENS})",
        "failOnError => false",
    ]
    if structured:
        args.append(f"responseFormat => {_quote(response_format())}")
    return f"ai_query({', '.join(args)})"


def retry_suffix(reason: str) -> str:
    return RETRY_SUFFIX.format(reason=reason)


def normalize_term(term: str) -> str:
    """Lowercase, spaces and hyphens to underscores, then the synonym table."""
    key = re.sub(r"[\s\-/]+", "_", term.strip().lower())
    return TERM_SYNONYMS.get(key, key)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _json_object(raw: str) -> str:
    """The JSON object in a reply, without a Markdown code fence some models add."""
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return fenced.group(1) if fenced else text


def normalize_answer(answer: dict) -> dict:
    """Normalize list terms and drop repeated list items (the first one's evidence is kept)."""
    out = dict(answer)
    for key, name in (("comorbidities", "term"), ("social_risk_factors", "factor")):
        items = answer.get(key)
        if not isinstance(items, list):
            continue
        seen, kept = set(), []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get(name), str):
                item = {**item, name: normalize_term(item[name])}
                if item[name] in seen:
                    continue
                seen.add(item[name])
            kept.append(item)
        out[key] = kept
    return out


def _evidence_items(answer: dict) -> list[tuple[str, bool, str | None]]:
    """(field, value is positive, evidence) for every value in a schema-valid answer."""
    pc = answer["primary_condition"]
    dd = answer["discharge_disposition"]
    items = [
        ("primary_condition", pc["value"] != "other", pc["evidence"]),
        ("discharge_disposition", dd["value"] != "not_stated", dd["evidence"]),
    ]
    for name in ("prior_admissions_mentioned", "follow_up_planned"):
        items.append((name, answer[name]["value"], answer[name]["evidence"]))
    for key in ("comorbidities", "social_risk_factors"):
        items += [(key, True, item["evidence"]) for item in answer[key]]
    return items


def evidence_problem(answer: dict, note: str) -> str | None:
    """The first evidence rule a schema-valid answer breaks, or None.

    A span matches when it is a substring of the note after runs of whitespace are collapsed to
    one space and both are lowercased; every other character, punctuation included, must match.
    MTSamples text has stray double spaces and models restart a quote with a capital letter, and
    neither changes what the quote says.
    """
    text = _collapse(note).lower()
    for name, positive, evidence in _evidence_items(answer):
        if evidence is None:
            if positive:
                return f"{name}: missing evidence"
            continue
        if len(evidence) > MAX_EVIDENCE_CHARS:
            return f"{name}: evidence longer than {MAX_EVIDENCE_CHARS} characters"
        if not evidence.strip() or _collapse(evidence).lower() not in text:
            return f"{name}: evidence is not a quote from the note: {evidence[:80]!r}"
    return None


def validate_answer(raw: str | None, note: str, error: str | None = None) -> Checked:
    """Check one model reply. `error` is ai_query's errorMessage for the row."""
    if error:
        return Checked(None, f"endpoint error: {error[:200]}")
    if raw is None or not raw.strip():
        return Checked(None, "empty response")
    try:
        answer = json.loads(_json_object(raw))
    except json.JSONDecodeError as e:
        return Checked(None, f"not JSON: {e.msg} at character {e.pos}")
    if not isinstance(answer, dict):
        return Checked(None, "not a JSON object")
    answer = normalize_answer(answer)
    problem = jsonschema.exceptions.best_match(SCHEMA_VALIDATOR.iter_errors(answer))
    if problem is not None:
        path = "/".join(str(p) for p in problem.absolute_path) or "answer"
        return Checked(None, f"schema: {path}: {problem.message[:200]}")
    problem = evidence_problem(answer, note)
    if problem:
        return Checked(None, problem, schema_valid=True)
    return Checked(answer, None, schema_valid=True)
