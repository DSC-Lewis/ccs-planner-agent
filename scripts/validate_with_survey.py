"""Validate our mock channel_metrics.json against the survey CSV.

Run::

    python -m scripts.validate_with_survey

It prints a side-by-side comparison of the mocked penetration and the
CSV-derived penetration for every mapped channel, and optionally writes the
merged values back to ``app/data/channel_metrics.json`` when ``--write`` is
passed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so ``app.*`` imports work when this is
# executed as a standalone script from a cloned repo.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import reference, survey_loader  # noqa: E402


def _fmt_pct(v: float | None) -> str:
    return f"{v:6.2f}%" if v is not None else "   n/a "


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",     default=str(survey_loader.DEFAULT_CSV))
    ap.add_argument("--mapping", default=str(survey_loader.DEFAULT_MAPPING))
    ap.add_argument("--write",   action="store_true",
                    help="overwrite penetration_pct in channel_metrics.json using CSV values")
    ap.add_argument("--tolerance", type=float, default=15.0,
                    help="percentage-point diff that still counts as PASS")
    args = ap.parse_args()

    overrides = survey_loader.channel_penetration_overrides(args.csv, args.mapping)
    metrics = reference.channel_metrics()

    print(f"=== Validating channel penetration against {Path(args.csv).name} ===")
    print(f"Tolerance: {args.tolerance:.1f} pp (pass within this delta)\n")

    header = f"{'channel_id':28s} {'mock':>8s} {'survey':>8s} {'delta':>8s}   result"
    print(header)
    print("-" * len(header))

    pass_count = fail_count = 0
    issues = []
    for channel_id, m in metrics.items():
        mock = m.penetration_pct
        survey_val = overrides.get(channel_id)
        if survey_val is None:
            print(f"{channel_id:28s} {_fmt_pct(mock)} {'no map':>8s} {'—':>8s}   skip")
            continue
        delta = survey_val - mock
        status = "PASS" if abs(delta) <= args.tolerance else "FAIL"
        if status == "PASS":
            pass_count += 1
        else:
            fail_count += 1
            issues.append((channel_id, mock, survey_val, delta))
        print(f"{channel_id:28s} {_fmt_pct(mock)} {_fmt_pct(survey_val)} {delta:+8.2f}   {status}")

    print("-" * len(header))
    print(f"Summary: {pass_count} pass, {fail_count} fail "
          f"(out of {pass_count + fail_count} mapped channels)")

    universe = survey_loader.estimated_universe(args.csv)
    if universe:
        print(f"Estimated universe from CSV: {universe:,} thousands")

    if args.write:
        target = reference.DATA_DIR if hasattr(reference, "DATA_DIR") else None
        target = Path(__file__).resolve().parent.parent / "app" / "data" / "channel_metrics.json"
        raw = json.loads(target.read_text(encoding="utf-8"))
        for channel_id, pct in overrides.items():
            if channel_id in raw:
                raw[channel_id]["penetration_pct"] = pct
        target.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote CSV-based penetration into {target.relative_to(ROOT)}")

    if issues:
        print("\nLargest divergences (|delta| > tolerance):")
        for cid, mock, sv, delta in sorted(issues, key=lambda x: -abs(x[3]))[:5]:
            print(f"  {cid:28s} mock={mock:.2f}% survey={sv:.2f}% delta={delta:+.2f}pp")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
