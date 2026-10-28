# AI-assisted development log

How this repository was built with AI agents, and the cases where a person or a test caught
something the AI got wrong or left open. Every case cites a PR and a file or commit on `main`
that shows it. Contoso Health is fictional.

## How the work ran

- A project lead agent owns the repository. It reads `SPEC.md`, splits it into one card per
  task, writes a brief for each, and dispatches an assistant agent into its own git worktree.
- The assistant builds on its own branch, runs the tests and reports back. The lead reviews the
  diff, sends findings back or merges. The factory then runs its gates and opens a draft PR per
  card (PRs #2 to #15), so history is one PR per task.
- Tests and CI gate every merge: `ruff`, `pytest` and, since this log, a gitleaks scan of the
  full history.
- Chris Teceno decided what an agent could not: the project's scope in `SPEC.md`, which models to
  compare for note extraction, that creating Azure resources waits for his approval, and where
  keys and tokens live (rotating the Databricks token by hand; the Census key in a secret
  scope, not in code). Nothing in this repository needed a secret in a commit; the scan result is
  at the end.

## Cases

Each case says what the AI produced, what caught the problem and what changed.

### 1. Pagination that could skip rows (PR #2)

The CMS client first advanced its offset by the requested page size. CMS can cap a page below
the requested limit, so every capped page would silently lose rows. Review rejected it. Commit
`23572e3` advances by rows received and raises `CmsApiError` when the rows collected differ from
the `count` the API reports; `tests/test_cms_client.py` covers both.

Outcome: corrected. The failure mode was silent data loss, which a green happy-path test does not
show.

### 2. "Census works without a key" (PR #7)

The lead told Chris that Census API calls work without a key at low volume. That was from
memory. The assistant building the ACS ingest tested it: api.census.gov now redirects keyless
data calls to `/data/missing_key.html` (only `variables.json` still answers). The key became
required, and `docs/data-sources.md` records the redirect and the fetch task's error for a
missing key.

Outcome: an AI claim corrected by running the call. It is also why the Census key sits in a
secret scope and, once Azure is approved, Key Vault.

### 3. Ingestion split into fetch and load, GitHub Actions fallback dropped (PRs #3, #4)

The spec first assumed Free Edition serverless might block data.cms.gov and api.census.gov, so
it split ingestion into a fetch step (pure Python, uploads raw files to a landing volume) and a
load step (reads the volume, never the internet), with a scheduled GitHub Actions workflow as the
fallback place to run fetch. A one-line probe from serverless then reached every source. The
probe is recorded in `SPEC.md` (commit `c580150`), which keeps the split, because landed raw
files make Bronze reproducible, and drops the fallback. The probe also showed a transient 503,
so fetch retries on 5xx.

Outcome: a design amended by evidence. Part of the plan was removed rather than built.

### 4. A paragraph added to the spec twice (commit `f0fe16f`)

Recording the probe result added the same `Probe result` paragraph twice: commits `7c4360c` and
`c580150` carry an identical line. Chris removed the duplicate in `f0fe16f`.

Outcome: a small AI editing slip, caught on read-through. It is listed because the spec is the
source of truth that every agent reads, so a stale or doubled paragraph is read as two
statements.

### 5. A Delta CHECK that fails on null (PR #7)

The first ACS Silver run failed. Delta treats a CHECK that evaluates to null as a violation, and
the 2011 vintage has a null uninsured percent, so `percent BETWEEN 0 AND 100` rejected the batch.
The fix wrote each constraint as `IS NULL OR ...`, and a test against a real Delta table (not a
mocked DataFrame) holds the behavior. `docs/governance.md` records the rule for any constraint on
a nullable column.

Outcome: corrected after a real run. Local unit tests with Spark alone had not exercised the
Delta constraint.

### 6. Penalty cutoffs kept explicitly approximate (PRs #3, #8)

From FY2019 CMS compares each hospital with the median of its dual-eligibility peer group, not
with 1.0. PR #3 found the peer groups and medians only in the FY2027 supplemental file. Using 1.0
for FY2019 to FY2026 would have given clean-looking, partly wrong flags. PR #8 keeps 1.0 as a
stand-in and marks those rows `cutoff_is_approximate = true`; `docs/gold.md` measures the error
where both cutoffs are known (899 of 14,575 FY2027 rows, 6.2 percent, fall on different sides).
The dashboard and Genie instructions repeat the caveat.

Outcome: a number kept honest instead of presented as exact. Superseded on 2026-10-28 (ADR
0008): research for a later run found the FY2019 to FY2025 supplemental files, which carry every
year's peer medians, and loading them replaced the stand-in except where CMS published no median.

### 7. Note-extraction model chosen by field F1, labels reviewed afterwards (PR #12)

Chris chose three Foundation Model endpoints to compare. The choice among them was made by
measurement: `databricks-gpt-oss-120b` scored mean field F1 0.81 against 0.70 and 0.54 on 50
labelled notes (`engagement/adr/0002-llm-risk-factor-extraction.md`). The labels were drafted
with AI help from the note text before any model ran, so every one was marked
`needs_review: true` and the first metrics were tagged `metrics_provisional=true` in MLflow. The
project lead then checked all 50 against the written rules; Chris Teceno, a clinician, reviewed
all 50 and decided the four judgment calls; one label changed (note 3360 gained `valvular_disease`). The saved predictions were
rescored on the reviewed labels without calling a model again: gpt-oss-120b moved from 0.82 to
0.81, the other two stayed at 0.70 and 0.54, and the choice held.

Outcome: accepted on evidence, with the limit written next to the number.

### 8. Genie answered the five client questions wrongly in round 1 (PR #14)

Each question went to the deployed Genie space and the answer was compared with a hand-written
query. No round 1 answer was fully right: Q1 filtered on a column that dropped 11 of 25 pairs,
Q3 joined to a table with one row per year and repeated predictions, Q4 called a facility
penalized for a condition it was not above the cutoff for, Q5 took the last run (one task only)
as the whole pipeline and reported no failed checks, and Q2 had correct SQL under a wrong
summary. `docs/genie.md` records each fix (instructions and example SQL), a reworded question
to show the fix was not tied to the sample wording, and two more rounds for problems round 2
still had.

Outcome: corrected. The tables were right in every final answer; the summaries were where Genie
was wrong, so the doc tells the reader to read the table under the summary.

### 9. The pre-registration commit lost in a squash (PRs #9, #11)

The analysis plan was committed on its own before outcomes were examined, as proof it came first.
The factory squash-merged PR #9 and deleted its branch, so on `main` the plan and the EDA are one
commit and the standalone commit is not in `main`'s history. It survives on the PR's head ref
(`refs/pull/9/head`), and `docs/causal-findings.md` (PR #11) cites its hash and
timestamps.

Outcome: a process gap found while writing the findings. The evidence was kept and cited rather
than restated from memory.

## What this log does not claim

- No accepted-unchanged count is kept. Most AI output was accepted after tests passed, and only
  the cases above changed something a reader would care about.
- Cases come from commits, merged docs and the PRs listed. Where a case rests on the lead
  agent's own account (case 2's first claim), it says so.

## Secret scanning

CI runs gitleaks over the full history (`.github/workflows/ci.yml`, action pinned to `v2.3.9`).
A local run on 2026-10-21 with gitleaks 8.30.1 (`gitleaks git -v .`) scanned 54 commits and found
no leaks, so no `.gitleaks.toml` allowlist exists.
