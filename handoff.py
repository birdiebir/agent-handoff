"""handoff 入口：python handoff.py [目標] [--list] [--session <id>] [--from <cli>] [--dry-run]"""
import sys

from handoff.cli import main

if __name__ == "__main__":
    sys.exit(main())
