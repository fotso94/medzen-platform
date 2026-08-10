#!/usr/bin/env python3
"""Use the proven dual-deadline control with the reviewed 4,500-second cap."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import b6_6_deadline as proven


WINDOW_SECONDS = 4500


def main() -> int:
    # Do not mutate the proven packet-016 module merely by importing this
    # successor wrapper. The cap changes only inside this executable process.
    previous = proven.WINDOW_SECONDS
    proven.WINDOW_SECONDS = WINDOW_SECONDS
    try:
        return proven.main()
    finally:
        proven.WINDOW_SECONDS = previous


if __name__ == "__main__":
    raise SystemExit(main())
