#!/usr/bin/env python3
"""
CLI runner for TradingView Continuation Rate Scanner.
Used by GitHub Actions or cron jobs.

Usage:
    python run_scan.py                          # All timeframes (dom extraction)
    python run_scan.py --timeframes 4H 1H 15min # Weekly scan
    python run_scan.py --timeframes 5min 1min   # Daily scan (Mon-Wed-Fri)
    python run_scan.py --method ocr             # Fallback to OCR
"""

import argparse
import os
import sys
import logging
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.scanner import TradingViewScanner
from src.config.assets import TIMEFRAMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="TradingView Continuation Rate Scanner"
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        choices=list(TIMEFRAMES.keys()),
        default=None,
        help="Timeframes to scan (default: all). E.g. --timeframes 5min 1min",
    )
    parser.add_argument(
        "--method",
        choices=["csv", "ocr", "ai_vision"],
        default=os.getenv("EXTRACTION_METHOD", "csv"),
        help="Extraction method (default: csv)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    tf_display = ", ".join(args.timeframes) if args.timeframes else "ALL"

    logger.info("=" * 60)
    logger.info("TradingView Continuation Rate Scanner")
    logger.info(f"Extraction method: {args.method}")
    logger.info(f"Timeframes: {tf_display}")
    logger.info("=" * 60)

    scanner = TradingViewScanner(
        headless=True,
        extraction_method=args.method,
        use_database=True,
        timeframe_filter=args.timeframes,
    )

    def progress(current, total, message):
        logger.info(f"[{current}/{total}] {message}")

    scanner.set_progress_callback(progress)

    try:
        results = scanner.run_full_scan()

        # Summary
        success = sum(
            1 for r in results
            if r.status == "success" and r.cont_rate is not None
        )
        failed = len(results) - success

        logger.info("=" * 60)
        logger.info(f"SCAN COMPLETE: {success} successful, {failed} failed")
        logger.info("=" * 60)

        # Print pivot table
        pivot = scanner.get_results_as_pivot()
        # Build header based on scanned timeframes
        tf_labels = args.timeframes or list(TIMEFRAMES.keys())
        header = f"{'Asset':<12} {'Category':<22}"
        for tf in tf_labels:
            header += f" {tf:>8}"
        header += f" {'Avg':>8}"

        logger.info(header)
        logger.info("-" * len(header))
        for row in pivot:
            line = f"{row['asset']:<12} {row['category']:<22}"
            for tf in tf_labels:
                val = row.get(tf)
                line += f" {f'{val:.1f}%' if val is not None else '—':>8}"
            avg = row.get("avg")
            line += f" {f'{avg:.1f}%' if avg is not None else '—':>8}"
            logger.info(line)

        if failed > 0:
            sys.exit(1)  # Signal partial failure to CI

    except Exception as e:
        logger.exception(f"Scan failed: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
