"""
cli.py — Command-line interface for the candidate transformer.

Usage:
  python3 -m transformer.cli --help
  python3 -m transformer.cli --csv sample_inputs/recruiter_export.csv
  python3 -m transformer.cli --ats sample_inputs/ats_candidates.json --github https://github.com/torvalds
  python3 -m transformer.cli --csv sample_inputs/recruiter_export.csv --config configs/ats_integration.yaml
  python3 -m transformer.cli --csv sample_inputs/recruiter_export.csv --output results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from transformer.adapters.ats_json_adapter import ATSJsonAdapter
from transformer.adapters.csv_adapter import CSVAdapter
from transformer.adapters.github_adapter import GitHubAdapter
from transformer.adapters.base import SourceAdapter
from transformer.pipeline import Pipeline
from transformer.projection.config import load_projection_config, default_projection_config

_PIPELINE_VERSION = "1.0.0"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transformer",
        description=(
            "Multi-Source Candidate Data Transformer\n"
            "Merges candidate data from CSV, ATS JSON, and GitHub into a canonical profile.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default schema, CSV only
  python3 -m transformer.cli --csv sample_inputs/recruiter_export.csv

  # ATS JSON + GitHub
  python3 -m transformer.cli --ats sample_inputs/ats_candidates.json --github https://github.com/torvalds

  # All three sources with custom projection
  python3 -m transformer.cli \\
    --csv sample_inputs/recruiter_export.csv \\
    --ats sample_inputs/ats_candidates.json \\
    --config configs/ats_integration.yaml \\
    --output results.json

  # Suppress provenance (clean output)
  python3 -m transformer.cli --csv sample_inputs/recruiter_export.csv --no-provenance
        """,
    )

    # Source inputs
    source_group = parser.add_argument_group("Source Inputs")
    source_group.add_argument(
        "--csv",
        metavar="FILE",
        help="Path to recruiter CSV export file",
    )
    source_group.add_argument(
        "--ats",
        metavar="FILE",
        help="Path to ATS JSON blob file",
    )
    source_group.add_argument(
        "--github",
        metavar="URL",
        nargs="+",
        help="One or more GitHub profile URLs (e.g. https://github.com/torvalds)",
    )

    # Config
    config_group = parser.add_argument_group("Configuration")
    config_group.add_argument(
        "--config",
        metavar="FILE",
        help="Path to projection config YAML (default: full canonical output)",
    )
    config_group.add_argument(
        "--no-provenance",
        action="store_true",
        help="Strip provenance from output",
    )
    config_group.add_argument(
        "--no-confidence",
        action="store_true",
        help="Strip confidence scores from output",
    )

    # Output
    output_group = parser.add_argument_group("Output")
    output_group.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Write output JSON to FILE instead of stdout",
    )
    output_group.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output (default: True)",
    )
    output_group.add_argument(
        "--summary",
        action="store_true",
        help="Print a human-readable summary table after the JSON output",
    )

    # Logging
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="Logging verbosity (default: WARNING)",
    )
    parser.add_argument(
        "--log-file",
        metavar="FILE",
        help="Write structured logs to FILE",
    )

    return parser


def setup_logging(level: str, log_file: Optional[str]) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=handlers,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    setup_logging(args.log_level, getattr(args, "log_file", None))

    # --- Build adapters ---
    adapters: List[SourceAdapter] = []

    if args.csv:
        adapters.append(CSVAdapter(args.csv))

    if args.ats:
        adapters.append(ATSJsonAdapter(args.ats))

    github_urls = getattr(args, "github", None) or []
    for url in github_urls:
        adapters.append(GitHubAdapter(url))

    if not adapters:
        print(
            "Error: at least one source is required (--csv, --ats, or --github).",
            file=sys.stderr,
        )
        parser.print_help(sys.stderr)
        return 1

    # --- Load projection config ---
    try:
        if args.config:
            config = load_projection_config(args.config)
        else:
            config = default_projection_config()

        # CLI flags override config file
        if args.no_provenance:
            config.provenance = False
        if args.no_confidence:
            config.confidence = False

    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # --- Run pipeline ---
    pipeline = Pipeline(adapters=adapters, config=config)
    result = pipeline.run()

    # --- Output ---
    indent = 2 if args.pretty else None
    output_json = json.dumps(result.to_dict(), indent=indent, default=str)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(output_json, encoding="utf-8")
        print(f"Output written to: {out_path}", file=sys.stderr)
    else:
        print(output_json)

    # --- Optional summary ---
    if args.summary:
        _print_summary(result)

    # Exit code: 1 if any candidates completely failed
    return 1 if result.candidates_failed > 0 else 0


def _print_summary(result) -> None:
    print("\n" + "=" * 60, file=sys.stderr)
    print("  Pipeline Summary", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Run ID          : {result.run_id}", file=sys.stderr)
    print(f"  Run At          : {result.run_at}", file=sys.stderr)
    print(f"  Sources         : {result.sources_attempted}", file=sys.stderr)
    print(f"  Candidates      : {result.candidates_total}", file=sys.stderr)
    print(f"  Failed          : {result.candidates_failed}", file=sys.stderr)
    print(f"  Schema Errors   : {len(result.validation_errors)}", file=sys.stderr)
    print(f"  Semantic Warns  : {len(result.validation_warnings)}", file=sys.stderr)

    if result.candidates:
        print("\n  Candidate Scores:", file=sys.stderr)
        for c in result.candidates:
            cid = c.get("candidate_id", "?")[:12]
            name = c.get("full_name", "(no name)")
            conf = c.get("overall_confidence", 0.0)
            emails = ", ".join(c.get("emails", []) or []) or "(no email)"
            bar = "█" * int(conf * 20) + "░" * (20 - int(conf * 20))
            print(f"    {cid}  {name:<25} [{bar}] {conf:.3f}  {emails}", file=sys.stderr)

    print("=" * 60 + "\n", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
