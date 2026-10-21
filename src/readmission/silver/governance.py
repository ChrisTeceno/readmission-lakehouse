"""Unity Catalog governance for Silver: table and column comments, tags and the analyst grant.

Column comments live in each table's StructType (`field` metadata), so the schema and its
documentation change together. See docs/governance.md.
"""

from dataclasses import dataclass

from pyspark.sql import types as T

# Analysts read Silver and Gold. The grant needs a Unity Catalog (account-level) group; see
# docs/governance.md for why it does not exist on Free Edition.
ANALYST_GROUP = "analysts"


@dataclass(frozen=True)
class TableDoc:
    comment: str
    tags: dict[str, str]


def field(
    name: str, data_type: T.DataType, comment: str, *, nullable: bool = True
) -> T.StructField:
    return T.StructField(name, data_type, nullable=nullable, metadata={"comment": comment})


def silver_tags(source: str, **extra: str) -> dict[str, str]:
    """No Silver table holds PHI: CMS data is facility level and MTSamples notes are public,
    de-identified transcription samples."""
    return {"layer": "silver", "contains_phi": "false", "source": source, **extra}


def _quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def comment_statements(table: str, schema: T.StructType, doc: TableDoc) -> list[str]:
    statements = [f"COMMENT ON TABLE {table} IS {_quote(doc.comment)}"]
    for f in schema:
        if "comment" in f.metadata:
            statements.append(
                f"ALTER TABLE {table} ALTER COLUMN `{f.name}` COMMENT {_quote(f.metadata['comment'])}"
            )
    return statements


def tag_statement(table: str, doc: TableDoc) -> str:
    pairs = ", ".join(f"{_quote(k)} = {_quote(v)}" for k, v in doc.tags.items())
    return f"ALTER TABLE {table} SET TAGS ({pairs})"


def grant_statement(table: str) -> str:
    return f"GRANT SELECT ON TABLE {table} TO `{ANALYST_GROUP}`"
