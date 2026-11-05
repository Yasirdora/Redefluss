#!/usr/bin/env python3
"""Load a German noun lexicon, used to check articles and plurals in the gender drill (gender_drill).
Replaces any lexicon loaded before.

The file is nouns.csv from gambolputty/german-nouns (CC BY-SA 4.0, built from Wiktionary): one row per
noun with the columns lemma, pos, genus (genus 1 to 4 for nouns with several) and the inflected forms,
among them nominativ plural (nominativ plural 1 to 4 and starred variants for nouns with several).
Proper names are skipped. A lemma listed twice (der See, die See) keeps both genders and all plurals.

    python3 scripts/import_nouns.py data/nouns/nouns.csv
    python3 scripts/import_nouns.py data/nouns/nouns.csv --db /tmp/test.db
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from german_tutor.engine import Engine  # noqa: E402

GENUS = {"m": "der", "f": "die", "n": "das"}
NAMES = ("Nachname", "Vorname", "Toponym", "Eigenname")


def read_nouns(path: Path) -> dict[str, tuple[set[str], list[str]]]:
    """lemma -> (articles, plurals) from the CSV."""
    nouns: dict[str, tuple[set[str], list[str]]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            lemma = (row.get("lemma") or "").strip()
            pos = row.get("pos") or ""
            if not lemma or "Substantiv" not in pos or any(name in pos for name in NAMES):
                continue
            articles = {GENUS[v.strip()] for k, v in row.items()
                        if k and k.startswith("genus") and v and v.strip() in GENUS}
            plurals = [v.strip() for k, v in row.items()
                       if k and k.startswith("nominativ plural") and v and v.strip() not in ("", "—", "-")]
            entry = nouns.setdefault(lemma, (set(), []))
            entry[0].update(articles)
            entry[1].extend(p for p in plurals if p not in entry[1])
    return nouns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--db", help="database path (default: data/private/tutor.db or $GERMAN_TUTOR_DB)")
    args = parser.parse_args()
    loaded = Engine(args.db).set_nouns(read_nouns(args.path))
    print(f"{loaded} nouns loaded.")


if __name__ == "__main__":
    main()
