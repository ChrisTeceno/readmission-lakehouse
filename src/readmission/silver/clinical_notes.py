"""silver_clinical_notes: MTSamples transcriptions, one row per note id.

MTSamples is a public set of de-identified sample transcriptions, not patient records. The
specialty values keep a leading space in the source (` Surgery`); Silver trims them.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

from readmission.silver.governance import TableDoc, field, silver_tags
from readmission.silver.quarantine import first_violation, missing_rules, raw_row, split_rows

TABLE = "silver_clinical_notes"
KEY = ["note_id"]

SILVER_CLINICAL_NOTES_SCHEMA = T.StructType(
    [
        field("note_id", T.IntegerType(), "MTSamples note id", nullable=False),
        field("medical_specialty", T.StringType(), "Specialty the sample is filed under"),
        field("sample_name", T.StringType(), "Title of the sample"),
        field("description", T.StringType(), "One-line summary of the note"),
        field("transcription", T.StringType(), "Full note text", nullable=False),
        field("keywords", T.StringType(), "Comma-separated keywords; null when there are none"),
        field("_batch_id", T.StringType(), "Bronze batch the row came from", nullable=False),
    ]
)

SILVER_CLINICAL_NOTES_DOC = TableDoc(
    comment=(
        "MTSamples clinical note transcriptions, one row per note, input to the LLM risk factor "
        "extraction. Public de-identified samples, no PHI. Source: bronze_clinical_notes."
    ),
    tags=silver_tags("mtsamples"),
)


def _blank_to_null(name: str):
    value = F.trim(F.col(name))
    return F.when(value != "", value)


def silver_clinical_notes(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Returns (silver_clinical_notes rows, quarantine rows). A note without text is useless to
    the extraction step, so it goes to quarantine."""
    df = bronze.withColumn("_raw", raw_row(bronze)).withColumn("note_id", F.col("id"))
    for name in ("medical_specialty", "sample_name", "description", "transcription", "keywords"):
        df = df.withColumn(name, _blank_to_null(name))
    rules = missing_rules(["note_id", "transcription"])
    checked = df.withColumn("_violation", first_violation(rules))
    return split_rows(checked, table=TABLE, schema=SILVER_CLINICAL_NOTES_SCHEMA, key=KEY)
