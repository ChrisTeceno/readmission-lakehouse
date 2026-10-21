import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Inline markdown links, with an optional #fragment or ?query.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def test_readme_relative_links_exist():
    text = (ROOT / "README.md").read_text()
    links = [
        target.split("#")[0]
        for target in LINK.findall(text)
        if not re.match(r"[a-z]+:", target) and not target.startswith("#")
    ]
    assert links
    missing = [link for link in links if not (ROOT / link).exists()]
    assert not missing, f"README links that do not resolve: {missing}"
