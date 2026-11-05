"""SQLite schema for the learner model."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "private" / "tutor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                 -- JSON
);
CREATE TABLE IF NOT EXISTS constructs (
    id          TEXT PRIMARY KEY,
    track       TEXT NOT NULL,          -- word_order | form | listening | reading | other
    stage       INTEGER NOT NULL,       -- acquisition order within word_order, else 0
    kind        TEXT NOT NULL,          -- grammar | function | listening | reading | other
    name        TEXT NOT NULL,
    rule        TEXT NOT NULL,
    example     TEXT NOT NULL DEFAULT '',
    impact      INTEGER NOT NULL,       -- ranking factors, 1-3
    relevance   INTEGER NOT NULL,
    treatable   INTEGER NOT NULL,       -- 0 = log but never drill
    level       INTEGER NOT NULL DEFAULT 0,   -- mastery ladder 0-5
    level_note  TEXT,                   -- e.g. free use waiting for a second judge
    next_review TEXT
);
CREATE TABLE IF NOT EXISTS units (
    id       TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    status   TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'in_progress', 'done')),
    data     TEXT NOT NULL              -- page extraction JSON
);
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    closed_at        TEXT,
    minutes_planned  INTEGER NOT NULL,
    plan             TEXT NOT NULL,     -- JSON list of blocks
    plan_pos         INTEGER NOT NULL DEFAULT 0,
    block_started_at TEXT NOT NULL,
    last_break_at    TEXT NOT NULL,
    engine_version   TEXT NOT NULL,     -- rules and tutor instructions in force
    fatigue          INTEGER,           -- self-rated, 1 fresh to 5 exhausted
    summary          TEXT               -- JSON, written by close_session
);
CREATE TABLE IF NOT EXISTS patterns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    construct_id TEXT NOT NULL REFERENCES constructs(id),
    trigger      TEXT NOT NULL,         -- lower case: the word that set the rule off (deswegen, weil, mit)
    first_at     TEXT NOT NULL,
    last_at      TEXT NOT NULL,         -- the latest error
    last_channel TEXT NOT NULL,
    stage        INTEGER NOT NULL DEFAULT 0,   -- retests passed since the latest error
    due          TEXT,                  -- date of the next retest; none once resolved
    resolved_at  TEXT,
    UNIQUE (construct_id, trigger)
);
CREATE TABLE IF NOT EXISTS attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    at             TEXT NOT NULL,
    construct_id   TEXT NOT NULL REFERENCES constructs(id),
    item           TEXT,
    context        TEXT NOT NULL CHECK (context IN ('recognise', 'recall', 'controlled', 'free')),
    channel        TEXT NOT NULL CHECK (channel IN ('typed', 'spoken', 'listening')),
    correct        INTEGER NOT NULL,             -- first-pass correct
    repair         TEXT NOT NULL DEFAULT 'none' CHECK (repair IN ('none', 'self', 'prompted')),
    certain        INTEGER NOT NULL DEFAULT 1,   -- 0 = unresolved: logged, never scored
    primed         INTEGER NOT NULL DEFAULT 0,   -- drilled earlier in the same session
    hint_level     INTEGER NOT NULL DEFAULT 0,   -- 0 none, 1 category, 2 location, 3 partial
    answer         TEXT,
    expected       TEXT,
    scorer         TEXT NOT NULL DEFAULT 'tutor', -- tutor | code:<checker> | examiner | human
    trigger        TEXT,                         -- lower case, as in patterns
    retest_of      INTEGER REFERENCES patterns(id),
    reason         TEXT                          -- the learner's reason, when asked: supports | contradicts | absent | unclear
);
CREATE TABLE IF NOT EXISTS errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id   INTEGER NOT NULL REFERENCES attempts(id),
    construct_id TEXT NOT NULL REFERENCES constructs(id),
    at           TEXT NOT NULL,
    sentence     TEXT,
    correction   TEXT,
    cause        TEXT,                  -- hypothesis; for listening and reading misses, one of a fixed set
    pattern_id   INTEGER REFERENCES patterns(id),
    anki_exported_at TEXT               -- when it went to Anki as a sentence card
);
CREATE TABLE IF NOT EXISTS words (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma            TEXT NOT NULL,     -- dictionary form: infinitive, nominative singular, or a whole chunk
    pos              TEXT NOT NULL CHECK (pos IN ('noun', 'verb', 'adjective', 'other', 'chunk')),
    article          TEXT CHECK (article IN ('der', 'die', 'das')),   -- nouns only
    gloss            TEXT NOT NULL,     -- English, in the sense first met
    example          TEXT,              -- the sentence it was met in
    source           TEXT,              -- e.g. book:B1-U3
    added_at         TEXT NOT NULL,
    anki_queued_at   TEXT,
    anki_exported_at TEXT,
    UNIQUE (lemma, pos)
);
CREATE TABLE IF NOT EXISTS word_evidence (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id    INTEGER NOT NULL REFERENCES words(id),
    session_id INTEGER REFERENCES sessions(id),
    at         TEXT NOT NULL,
    facet      TEXT NOT NULL CHECK (facet IN ('meaning', 'hearing', 'gender', 'use')),
    correct    INTEGER NOT NULL,
    primed     INTEGER NOT NULL DEFAULT 0,   -- shown, glossed or corrected earlier in the same session
    scorer     TEXT NOT NULL,                -- code:word_check | tutor
    answer     TEXT
);
CREATE TABLE IF NOT EXISTS word_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    at          TEXT NOT NULL,
    purpose     TEXT NOT NULL CHECK (purpose IN ('check', 'preview')),
    source      TEXT,
    items       TEXT NOT NULL,          -- JSON: questions with their keys, and the words only glossed or shown
    answered_at TEXT                    -- from then on, every word in it counts as shown for the session
);
CREATE TABLE IF NOT EXISTS frequency (
    word TEXT PRIMARY KEY,              -- lower case
    rank INTEGER NOT NULL               -- 1 = most frequent
);
CREATE TABLE IF NOT EXISTS samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('speech', 'writing')),
    text       TEXT NOT NULL,              -- the learner's words, uncorrected
    audio_path TEXT,
    note       TEXT
);
CREATE TABLE IF NOT EXISTS measures (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    kind      TEXT NOT NULL,            -- see catalog.MEASURE_KINDS
    part      TEXT,                     -- lesen | hoeren | schreiben | sprechen, for mocks
    rater     TEXT,                     -- answer_key | human | model; gates use only the first two
    sample_id INTEGER REFERENCES samples(id),   -- what a human_rating rated, for calibrating the examiner
    score     REAL NOT NULL,
    max_score REAL,                     -- none for open-ended measures such as fluency
    note      TEXT
);
CREATE INDEX IF NOT EXISTS attempts_by_construct ON attempts (construct_id, context, at);
CREATE INDEX IF NOT EXISTS errors_by_time ON errors (at);
CREATE INDEX IF NOT EXISTS evidence_by_word ON word_evidence (word_id, facet, at);
"""

# Columns added after the first databases were created (engine 0.3.0); connect() adds any that are missing.
ADDED_COLUMNS = [
    ("attempts", "trigger", "TEXT"),
    ("attempts", "retest_of", "INTEGER REFERENCES patterns(id)"),
    ("errors", "pattern_id", "INTEGER REFERENCES patterns(id)"),
    ("attempts", "reason", "TEXT"),
    ("errors", "anki_exported_at", "TEXT"),
]


def db_path(path: str | os.PathLike | None = None) -> str:
    """The database to open: `path`, else GERMAN_TUTOR_DB, else data/private/tutor.db."""
    return str(path or os.environ.get("GERMAN_TUTOR_DB") or DEFAULT_DB)


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and if needed create or upgrade) the learner database."""
    path = db_path(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    with conn:
        for table, column, decl in ADDED_COLUMNS:
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return conn
