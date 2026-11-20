from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from gap_config import get_settings
from gap_config.storage import local_directory, local_file

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run GAP pricing with filesystem or S3 inputs and outputs")
    parser.add_argument("--config", help="JSON configuration file (overrides GAP_CONFIG_FILE)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ctr-dir", help="Input directory or s3://bucket/prefix")
    source.add_argument("--ctr-file", action="append", help="Input file or S3 object URI; repeat for multiple files")
    parser.add_argument("--basis-dir", help="Explicit snapshot directory or S3 prefix")
    parser.add_argument("--assumption-dir", help="Explicit snapshot directory or S3 prefix")
    parser.add_argument("--output-dir", help="Output directory or s3://bucket/prefix")
    parser.add_argument("--workers", type=int, help="Concurrent pricing workers")
    parser.add_argument("--report", choices=("xlsx", "html", "parquet"), default="xlsx")
    parser.add_argument("--check-config", action="store_true", help="Validate configuration and writable working directories without pricing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.config:
            os.environ["GAP_CONFIG_FILE"] = str(Path(args.config).resolve())
        get_settings.cache_clear()
        settings = get_settings()
        settings.validate_runtime()
        if args.workers is not None and args.workers < 1:
            raise ValueError("--workers must be a positive integer")
        if args.check_config:
            logger.info("Configuration valid: %s", settings.config_file)
            return 0
        from ctr_loader.engine_paths import snapshot_dir
        basis_dir = local_directory(args.basis_dir) if args.basis_dir else snapshot_dir("basis")
        assumption_dir = local_directory(args.assumption_dir) if args.assumption_dir else snapshot_dir("assumption")
        if args.ctr_file:
            inputs = [local_file(path) for path in args.ctr_file]
        else:
            input_dir = local_directory(args.ctr_dir or settings.input_dir)
            inputs = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".ctr")
        if not inputs:
            raise ValueError("No CTR input files found")
        from .concurrent_runner import load_shared_snapshots, run_quotes_concurrent
        basis, assumptions = load_shared_snapshots(basis_dir, assumption_dir)
        report_mode = {"xlsx": True, "html": "html", "parquet": False}[args.report]
        summary = run_quotes_concurrent(
            inputs, basis, assumptions, args.output_dir or settings.output_dir,
            basis_snapshot=basis_dir.name, assump_snapshot=assumption_dir.name,
            max_workers=args.workers, write_report=report_mode, wait_for_reports=True,
            basis_folder=str(basis_dir), assump_folder=str(assumption_dir),
        )
        failures = summary.filter(summary["status"] != "OK")
        if failures.height:
            for row in failures.iter_rows(named=True):
                logger.error("Quote failed: %s: %s", row["ctr_name"], row["error"])
            return 1
        logger.info("Completed and published %s quote(s)", summary.height)
        return 0
    except Exception:
        logger.exception("Pricing job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
