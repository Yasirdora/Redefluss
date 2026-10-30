#!/usr/bin/env python3
"""Load a German word-frequency list, used to rank word-check candidates (common words get tested,
rare ones only glossed). Replaces any list loaded before.

The file has one word per line, most frequent first, optionally followed by a count, as in the
OpenSubtitles lists (de_50k.txt: "ich 5413921"). Subtitles are close to spoken German, which suits
the learner's goal.

    python3 scripts/import_frequency.py path/to/de_50k.txt
    python3 scripts/import_frequency.py path/to/de_50k.txt --db /tmp/test.db
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from german_tutor.engine import COMMON_RANK, Engine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--db", help="database path (default: data/private/tutor.db or $GERMAN_TUTOR_DB)")
    args = parser.parse_args()
    words = [line.split()[0] for line in args.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    loaded = Engine(args.db).set_frequency(words)
    print(f"{loaded} words loaded; the top {min(loaded, COMMON_RANK)} count as common.")


if __name__ == "__main__":
    main()
