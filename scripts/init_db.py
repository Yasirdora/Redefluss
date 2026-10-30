#!/usr/bin/env python3
"""Create the learner database and load the catalog, book units, profile and past sessions.

Safe to re-run: the catalog and units are upserted, and a session already imported
(same start time) is skipped.

    python3 scripts/init_db.py                      # uses data/private/
    python3 scripts/init_db.py --db /tmp/test.db --profile data/profile.example.json --sessions
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from german_tutor.engine import Engine  # noqa: E402

PRIVATE = ROOT / "data" / "private"


class FixedClock:
    """Lets past sessions be replayed with their original timestamps."""

    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def import_session(engine: Engine, path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(data["started_at"])
    if engine.conn.execute("SELECT 1 FROM sessions WHERE started_at = ?",
                           (start.isoformat(timespec="seconds"),)).fetchone():
        return f"{path.name}: already imported"
    clock = FixedClock(start)
    real_clock, engine.clock = engine.clock, clock
    try:
        session_id = engine.start_session(data["minutes"])["session_id"]
        for attempt in data["attempts"]:
            attempt = dict(attempt)
            clock.at = start + timedelta(minutes=attempt.pop("minute", 0))
            engine.log_attempt(session_id, **attempt)
        clock.at = start + timedelta(minutes=data["minutes"])
        engine.close_session(session_id, len(data["attempts"]), notes=data.get("notes"))
    finally:
        engine.clock = real_clock
    return f"{path.name}: imported {len(data['attempts'])} attempts as session {session_id}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="database path (default: data/private/tutor.db or $GERMAN_TUTOR_DB)")
    parser.add_argument("--profile", type=Path, default=PRIVATE / "profile.json")
    parser.add_argument("--sessions", type=Path, nargs="*", default=sorted(PRIVATE.glob("session-*.json")),
                        help="past-session files to import (default: data/private/session-*.json)")
    args = parser.parse_args()

    engine = Engine(args.db)
    engine.bootstrap()
    print(f"catalog and {engine.conn.execute('SELECT COUNT(*) FROM units').fetchone()[0]} book unit(s) loaded")
    if args.profile.exists():
        engine.set_profile(json.loads(args.profile.read_text(encoding="utf-8")))
        print(f"profile loaded from {args.profile.name}")
    for path in args.sessions:
        print(import_session(engine, path))


if __name__ == "__main__":
    main()
