#!/usr/bin/env python3
"""Start the German tutor MCP server:  python3 /path/to/tutor/server.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from german_tutor.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
