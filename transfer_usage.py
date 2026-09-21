"""Export anonymous station counters or combine exports without cloud uploads."""

import argparse
import json
from pathlib import Path

from transfer_budget import aggregate_snapshots, get_transfer_meter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--combine", type=Path, nargs="+")
    args = parser.parse_args(argv)
    result = (
        aggregate_snapshots(
            [json.loads(path.read_text(encoding="utf-8")) for path in args.combine]
        )
        if args.combine
        else get_transfer_meter().snapshot()
    )
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result.get("available", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
