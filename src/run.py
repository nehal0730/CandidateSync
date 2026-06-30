"""
run.py
======
CLI entry point for the Multi-Source Candidate Data Transformer.

Usage
-----
  python run.py --inputs file1.csv file2.json file3.pdf --output out.json
  python run.py --inputs file1.csv file2.json --config configs/minimal.json --output out.json
  python run.py --inputs sample_inputs/*.* --output outputs/default.json --pretty

Run `python run.py --help` for all options.
"""
import argparse
import glob
import json
import logging
import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(__file__))

from transformer import run_pipeline, load_config
from pipeline.validator import ValidationError
from pipeline.projector import ProjectionError


def _expand_inputs(patterns: List[str]) -> List[str]:
    """Expand glob patterns and de-duplicate, preserving order."""
    paths: List[str] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(sorted(matches))
        elif os.path.exists(pattern):
            paths.append(pattern)
        else:
            print(f"[warn] No file matched pattern: {pattern}", file=sys.stderr)
    # de-duplicate, preserve order
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multi-Source Candidate Data Transformer — turns messy "
                    "candidate data (CSV / ATS JSON / Resume PDF/DOCX / Notes TXT) "
                    "into one canonical, confidence-scored profile per candidate.",
    )
    parser.add_argument(
        "--inputs", "-i", nargs="+", required=True,
        help="Input file paths or glob patterns (e.g. sample_inputs/*.csv)",
    )
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to a runtime projection config JSON file. "
            "If omitted, the full default canonical schema is emitted.",
    )
    parser.add_argument(
        "--output", "-o", default="outputs/result.json",
        help="Path to write the output JSON (default: outputs/result.json)",
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="Pretty-print the output JSON with indentation.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Raise an error if output fails schema validation "
            "(default: log warnings and continue).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose (INFO-level) logging.",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress all logging except errors.",
    )

    args = parser.parse_args()

    # ── Logging setup ────────────────────────────────────────────────────────
    level = logging.WARNING
    if args.verbose:
        level = logging.INFO
    if args.quiet:
        level = logging.ERROR
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    # ── Resolve inputs ───────────────────────────────────────────────────────
    input_paths = _expand_inputs(args.inputs)
    if not input_paths:
        print("[error] No valid input files found.", file=sys.stderr)
        return 1

    print(f"→ Processing {len(input_paths)} input file(s):")
    for p in input_paths:
        print(f"    • {p}")

    # ── Load config ──────────────────────────────────────────────────────────
    config = load_config(args.config)
    if args.config:
        print(f"→ Using config: {args.config}")
    else:
        print("→ No config provided — emitting full default canonical schema.")

    # ── Run pipeline ──────────────────────────────────────────────────────────
    try:
        results = run_pipeline(
            input_paths, config=config, strict_validation=args.strict
        )
    except (ProjectionError, ValidationError) as exc:
        print(f"[error] Pipeline failed: {exc}", file=sys.stderr)
        return 2

    if not results:
        print("[warn] No candidate records produced.", file=sys.stderr)

    print(f"→ Produced {len(results)} candidate profile(s).")

    # ── Write output ──────────────────────────────────────────────────────────
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        if args.pretty:
            json.dump(results, f, indent=2, ensure_ascii=False)
        else:
            json.dump(results, f, ensure_ascii=False)

    print(f"✓ Output written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())