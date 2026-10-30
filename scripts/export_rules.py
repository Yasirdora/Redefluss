#!/usr/bin/env python3
"""Write the tutor's instructions as an Antigravity workspace rule.

The MCP server sends the same text when a client connects, but not every client passes it to the
model. Re-run this after changing INSTRUCTIONS in german_tutor/mcp_server.py.

    python3 scripts/export_rules.py            # writes ../.agents/rules/german-tutor.md
    python3 scripts/export_rules.py --check    # exit 1 if that file is missing or out of date
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from german_tutor.mcp_server import rule_file  # noqa: E402

DEFAULT_OUT = ROOT.parent / ".agents" / "rules" / "german-tutor.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}")
    parser.add_argument("--check", action="store_true", help="only report whether the file is current")
    args = parser.parse_args()

    text = rule_file()
    current = args.out.exists() and args.out.read_text(encoding="utf-8") == text
    if args.check:
        print(f"{args.out}: {'up to date' if current else 'missing or out of date'}")
        sys.exit(0 if current else 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
