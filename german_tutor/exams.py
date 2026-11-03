"""Exam formats: what each B2 exam looks like, and which one a learner is preparing for.

Each format lives in data/exams/<id>.json with its sources: the parts and their points, the pass and
retake rules, the exam gates, and what exam-format reading, listening, writing and speaking practice
looks like. The learner names their exam in their own words ("telc", "the BAMF job course"); detect()
maps that to a format, and says nothing rather than guess when the words fit none or several.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EXAMS_DIR = Path(__file__).resolve().parent.parent / "data" / "exams"
GENERAL = "general_b2"   # practice without a set exam, or while the exam is unclear
NO_EXAM = "none"         # what the learner said when there is no exam
LEVEL = re.compile(r"\b([abc][12])\b")


def load(directory: Path = EXAMS_DIR) -> dict[str, dict]:
    exams = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        exams[data["id"]] = data
    return exams


EXAMS = load()
CHOICES = tuple(e for e in EXAMS if e != GENERAL) + (NO_EXAM,)
PARTS = tuple(dict.fromkeys(part for exam in EXAMS.values() for part in exam["parts"]))


def detect(*texts: str | None) -> str | None:
    """The B2 exam named in the learner's words, or None when they name none, another level, or several."""
    text = " ".join(t for t in texts if t).lower()
    levels = set(LEVEL.findall(text))
    if levels and "b2" not in levels:
        return None
    found = [exam["id"] for exam in EXAMS.values() if exam.get("detect")
             and any(word in text for word in exam["detect"]["any"])
             and not any(word in text for word in exam["detect"].get("none", []))]
    return found[0] if len(found) == 1 else None


def get(exam_id: str | None) -> dict:
    """The format for an id; the general one for None, 'none' or anything unknown."""
    return EXAMS.get(exam_id or GENERAL) or EXAMS[GENERAL]
