"""Compare Contoso's synthetic data with public benchmarks and print the table.

    uv run python scripts/check_synthetic_realism.py           # print the table
    uv run python scripts/check_synthetic_realism.py --write   # also refresh docs/synthetic-data.md

Runs locally: the synthetic values come from the seeded generator, and the benchmarks are the
public figures recorded with their sources in `readmission.analysis.realism`. `--write`
replaces the table between the markers in docs/synthetic-data.md; `tests/test_realism.py`
fails if the committed table and this output differ.
"""

import argparse
from pathlib import Path

from readmission.analysis.realism import checks, markdown

DOC = Path(__file__).parent.parent / "docs" / "synthetic-data.md"
BEGIN = "<!-- realism-check:begin -->"
END = "<!-- realism-check:end -->"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="refresh docs/synthetic-data.md")
    args = parser.parse_args()
    table = markdown(checks())
    print(table)
    if args.write:
        text = DOC.read_text()
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        DOC.write_text(f"{head}{BEGIN}\n{table}\n{END}{tail}")
        print(f"wrote {DOC}")


if __name__ == "__main__":
    main()
