from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.collectors.collect import collect_all, to_seed_rows


def main() -> int:
    p = argparse.ArgumentParser(description="Collect jobs/courses/volunteering into JSON.")
    p.add_argument("--query", default="python", help="Search keyword(s), e.g. 'python junior lima'")
    p.add_argument("--limit", type=int, default=60, help="Max total items")
    p.add_argument(
        "--out",
        default="backend/data/opportunities_collected.json",
        help="Output JSON file (relative to repo root is OK)",
    )
    args = p.parse_args()

    items = collect_all(query=args.query, limit_total=args.limit)
    rows = to_seed_rows(items)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} items -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

