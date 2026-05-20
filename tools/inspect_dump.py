"""
Selector discovery helper. Loads a saved dump from dumps/, searches for text
snippets we want to capture (e.g. "Posted X ago", "N jobs posted", "$N spent"),
and prints the DOM ancestor chain of each match with data-test / data-qa
attributes highlighted -- the stable hooks we should use as selectors.

Usage:
    python tools/inspect_dump.py dumps/search-*.html
    python tools/inspect_dump.py dumps/detail-~02....html
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag


# Targets per page type. Each target = (label, regex-of-text-to-match).
# Regexes match the text content of leaf elements after stripping whitespace.
SEARCH_TARGETS = [
    # Looser: anything containing "ago" or "yesterday" as a standalone short string
    ("post_time", re.compile(r"^(yesterday|last\s+\w+|\d+\s+\w+\s+ago|just\s+now|posted\s+\d+\s+\w+\s+ago)$", re.I)),
    ("post_time_loose", re.compile(r"\bago\b|^yesterday$", re.I)),
    ("card_location", re.compile(r"^(United States|United Kingdom|Canada|Australia|Germany|France|India|Pakistan|Philippines|Ukraine|Spain|Italy|Netherlands|Sweden|Brazil|Mexico|Poland|Portugal|Singapore|Israel|Argentina|UAE|Switzerland|Romania|Turkey|Egypt|South Africa)$")),
]

DETAIL_TARGETS = [
    # Loosened to catch variations like "5 jobs posted", "12 Jobs posted"
    ("client_jobs_posted", re.compile(r"^\d[\d,]*\s+jobs?\s+posted", re.I)),
    ("jobs_posted_loose", re.compile(r"\d+\s+jobs?\s+posted", re.I)),
    ("client_hire_rate", re.compile(r"\d+%.*hire\s+rate", re.I)),
    ("client_hourly_rate", re.compile(r"\$\d[\d.]*\s*/?\s*hr", re.I)),
    ("client_total_spent", re.compile(r"total\s+spent", re.I)),
    ("member_since", re.compile(r"member\s+since", re.I)),
    ("proposals", re.compile(r"^(Less than 5|5 to 10|10 to 15|15 to 20|20 to 50|50\+)$")),
    # Fallback: any spend-looking string (e.g. "$50K+" alone)
    ("spend_value", re.compile(r"^\$\d[\d.,]*[kKmM]?\+?$")),
]


def attr_summary(tag: Tag) -> str:
    """Return a short attr-only summary of a tag (data-test, data-qa, etc.)."""
    interesting = ("data-test", "data-qa", "data-cy", "data-ev-label", "id")
    parts = [tag.name]
    for k in interesting:
        v = tag.get(k)
        if v:
            parts.append(f'{k}="{v}"')
    cls = tag.get("class")
    if cls:
        # Trim to the first two class tokens to keep readable.
        parts.append("class=" + ".".join(cls[:2]))
    return " ".join(parts)


def stable_selector_chain(tag: Tag, depth: int = 6) -> str:
    """Build a CSS-like chain of the first `depth` ancestors that have
    a data-test / data-qa hook -- these are the selectors worth pinning."""
    parts: list[str] = []
    cur: Tag | None = tag
    n = 0
    while cur is not None and n < depth:
        hooks = []
        for k in ("data-test", "data-qa", "data-cy"):
            v = cur.get(k) if isinstance(cur, Tag) else None
            if v:
                hooks.append(f'[{k}="{v}"]')
        if hooks:
            parts.append(cur.name + "".join(hooks))
            n += 1
        cur = cur.parent if isinstance(cur, Tag) else None
    # Outer-first
    return " ".join(reversed(parts)) if parts else "(no stable hooks in ancestor chain)"


def inspect(path: Path, targets: list[tuple[str, re.Pattern]]) -> None:
    print(f"\n=== {path.name} ===")
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    # Walk all leaf-ish text containers.
    candidates = soup.find_all(string=True)
    seen_per_target: dict[str, int] = {label: 0 for label, _ in targets}
    for label, pattern in targets:
        print(f"\n  -- target: {label}  pattern={pattern.pattern!r}")
        hits = 0
        for text in candidates:
            stripped = text.strip()
            if not stripped:
                continue
            if not pattern.match(stripped):
                continue
            hits += 1
            parent = text.parent
            if not isinstance(parent, Tag):
                continue
            print(f"     text: {stripped!r}")
            print(f"     leaf: {attr_summary(parent)}")
            print(f"     stable chain: {stable_selector_chain(parent)}")
            if hits >= 5:
                print(f"     (... stopping after 5 matches)")
                break
        if hits == 0:
            print("     [no matches]")
        seen_per_target[label] = hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+",
                    help="Dump file paths (globs OK)")
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        matches = [Path(m) for m in glob.glob(p)]
        if not matches and Path(p).exists():
            matches = [Path(p)]
        files.extend(matches)

    if not files:
        print("No matching dump files.", file=sys.stderr)
        return 1

    for f in files:
        if "search-" in f.name:
            inspect(f, SEARCH_TARGETS)
        else:
            inspect(f, DETAIL_TARGETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
