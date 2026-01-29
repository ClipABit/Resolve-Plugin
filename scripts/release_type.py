from __future__ import annotations

import os
import sys


def main() -> int:
    release_type = os.getenv("RELEASE_TYPE", "").strip().lower()
    if not release_type:
        return 0

    if release_type not in {"patch", "minor", "major"}:
        print(f"Invalid RELEASE_TYPE: {release_type}", file=sys.stderr)
        return 1

    print(release_type)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
