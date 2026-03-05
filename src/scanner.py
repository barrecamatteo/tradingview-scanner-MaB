"""
Main scanner orchestrator: Coordinates browser, navigation, extraction, and database.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Dict, Callable, Optional

from .scraper.browser import TradingViewBrowser
from .scraper.navigator import ChartNavigator
from .config.assets import get_all_assets, get_timeframes, SCRAPER_CONFIG

try:
    from .database.supabase_client import SupabaseDB
except ImportError:
    SupabaseDB = None

logger = logging.getLogger(__name__)


class ScanResult:
    """Container for a single scan result."""

    def __init__(
        self,
        asset: str,
        category: str,
        timeframe: str,
        cont_rate: Optional[float] = None,
        confidence: float = 0.0,
        status: str = "success",
        error: str = None,
    ):
        self.asset = asset
        self.category = category
        self.timeframe = timeframe
        self.cont_rate = cont_rate
        self.confidence = confidence
        self.status = status
        self.error = error

    def to_dict(self) -> Dict:
        return {
            "asset": self.asset,
            "category": self.category,
            "timeframe": self.timeframe,
            "cont_rate": self.cont_rate,
            "confidence": self.confidence,
            "status": self.status,
            "error_message": self.error,
        }


class TradingViewScanner:
    """
    Main scanner that orchestrates the full scraping pipeline:
    1. Open browser & login
    2. Iterate through all asset/timeframe combinations
    3. Extract Cont. Rate from each
    4. Store results in database
    """

    def __init__(
        self,
        headless: bool = True,
        extraction_method: str = "csv",
        use_database: bool = True,
        timeframe_filter: List[str] = None,
    ):
        self.headless = headless
        self.extraction_method = extraction_method
        self.use_database = use_database
        self.timeframe_filter = timeframe_filter

        self.browser: Optional[TradingViewBrowser] = None
        self.navigator: Optional[ChartNavigator] = None
        self.extractor: Optional[ContRateExtractor] = None
        self.db: Optional[SupabaseDB] = None

        self.results: List[ScanResult] = []
        self._progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Callable):
        """Set a callback function for progress updates: callback(current, total, message)"""
        self._progress_callback = callback

    def _report_progress(self, current: int, total: int, message: str):
        """Report progress via callback if set."""
        if self._progress_callback:
            self._progress_callback(current, total, message)
        logger.info(f"[{current}/{total}] {message}")

    def run_full_scan(self) -> List[ScanResult]:
        """
        Execute a complete scan of all assets and timeframes.
        Respects timeframe_filter if set.
        Returns list of ScanResult objects.
        """
        assets = get_all_assets()
        timeframes = get_timeframes(self.timeframe_filter)
        total = len(assets) * len(timeframes)
        self.results = []
        scan_id = None
        successful = 0
        failed = 0

        try:
            # Initialize components
            self._report_progress(0, total, "Initializing browser...")

            # Set up download directory for CSV extraction
            self._download_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "data", "downloads"
            )
            self._download_dir = os.path.abspath(self._download_dir)
            os.makedirs(self._download_dir, exist_ok=True)

            self.browser = TradingViewBrowser(
                headless=self.headless,
                download_dir=self._download_dir,
            )
            self.navigator = ChartNavigator(
                self.browser.get_driver(),
                download_dir=self._download_dir,
            )

            # Only init OCR extractor if needed
            if self.extraction_method in ("ocr", "ai_vision"):
                from .scraper.extractor import ContRateExtractor
                self.extractor = ContRateExtractor(method=self.extraction_method)

            if self.use_database and SupabaseDB is not None:
                self.db = SupabaseDB()
                scan_id = self.db.start_scan(total)

            # Login
            self._report_progress(0, total, "Logging in to TradingView...")
            if not self.browser.login():
                raise RuntimeError("Failed to login to TradingView")

            # Dismiss any popups
            self.navigator.dismiss_popups()

            # Load chart page once to establish layout with indicators
            self._report_progress(0, total, "Loading chart layout...")
            if not self.navigator.initial_load():
                raise RuntimeError("Failed to load chart page")

            # Iterate: timeframe first, then assets (minimizes timeframe switches)
            current = 0
            for tf_label, tf_interval in timeframes.items():
                for category, symbol, asset_name in assets:
                    current += 1
                    self._report_progress(
                        current, total,
                        f"Scanning {asset_name} @ {tf_label}..."
                    )

                    result = self._scan_single(
                        category, symbol, asset_name, tf_label, tf_interval
                    )
                    self.results.append(result)

                    if result.status == "success" and result.cont_rate is not None:
                        successful += 1
                    else:
                        failed += 1

                    # Save to database incrementally
                    if self.db:
                        self._save_result(result, scan_id)

            # Complete scan log
            if self.db and scan_id:
                self.db.complete_scan(scan_id, successful, failed)

            self._report_progress(
                total, total, f"Scan complete! {successful}/{total} successful"
            )

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            if self.db and scan_id:
                self.db.complete_scan(scan_id, successful, failed, str(e))
            raise

        finally:
            if self.browser:
                self.browser.close()

        return self.results

    def _scan_single(
        self,
        category: str,
        symbol: str,
        asset_name: str,
        tf_label: str,
        tf_interval: str,
    ) -> ScanResult:
        """Scan a single asset/timeframe combination with retries.
        
        Uses progressive wait times: 4s -> 8s -> 10s for indicator loading.
        """
        retry_count = SCRAPER_CONFIG["retry_count"]
        wait_times = [4, 8, 10]  # Progressive indicator wait per attempt

        for attempt in range(retry_count):
            try:
                indicator_wait = wait_times[min(attempt, len(wait_times) - 1)]

                # Navigate to chart
                if not self.navigator.navigate_to_chart(
                    symbol, tf_interval, indicator_wait=indicator_wait
                ):
                    if attempt < retry_count - 1:
                        logger.warning(
                            f"Navigation failed for {asset_name}@{tf_label}, "
                            f"retry {attempt + 1}/{retry_count}"
                        )
                        time.sleep(3)
                        continue
                    return ScanResult(
                        asset=asset_name,
                        category=category,
                        timeframe=tf_label,
                        status="error",
                        error="Navigation failed after retries",
                    )

                # Dismiss popups that might have appeared
                self.navigator.dismiss_popups()

                # Extract Cont. Rate based on method
                if self.extraction_method == "csv":
                    # CSV extraction: download chart data, parse last row
                    cont_rate, confidence = self.navigator.get_cont_rate_from_csv(
                        asset_name=asset_name, timeframe=tf_label
                    )
                else:
                    # OCR/AI Vision fallback
                    screenshot = self.navigator.get_analysis_panel_screenshot()
                    cont_rate, confidence = self.extractor.extract_cont_rate(
                        screenshot,
                        asset_name=asset_name,
                        timeframe=tf_label,
                    )

                if cont_rate is not None:
                    return ScanResult(
                        asset=asset_name,
                        category=category,
                        timeframe=tf_label,
                        cont_rate=cont_rate,
                        confidence=confidence,
                    )
                elif attempt < retry_count - 1:
                    logger.warning(
                        f"Extraction failed for {asset_name}@{tf_label}, "
                        f"retry {attempt + 1}/{retry_count}"
                    )
                    time.sleep(2)

            except Exception as e:
                logger.error(f"Error scanning {asset_name}@{tf_label}: {e}")
                if attempt < retry_count - 1:
                    time.sleep(3)

        return ScanResult(
            asset=asset_name,
            category=category,
            timeframe=tf_label,
            status="error",
            error="Extraction failed after retries",
        )

    def _save_result(self, result: ScanResult, scan_id: str = None):
        """Save a scan result to the database."""
        try:
            # Upsert current rate
            self.db.upsert_rate(
                asset=result.asset,
                category=result.category,
                timeframe=result.timeframe,
                cont_rate=result.cont_rate,
                confidence=result.confidence,
                status=result.status,
                error_message=result.error,
            )

            # Add to history
            self.db.add_history(
                asset=result.asset,
                category=result.category,
                timeframe=result.timeframe,
                cont_rate=result.cont_rate,
                confidence=result.confidence,
                scan_batch_id=scan_id,
            )
        except Exception as e:
            logger.error(
                f"Failed to save result for {result.asset}@{result.timeframe}: {e}"
            )

    def get_results_as_pivot(self) -> List[Dict]:
        """Convert scan results to pivot table format."""
        all_tf_labels = ["4H", "1H", "15min", "5min", "1min"]
        asset_data = {}

        for r in self.results:
            if r.asset not in asset_data:
                asset_data[r.asset] = {
                    "asset": r.asset,
                    "category": r.category,
                }
                for tf in all_tf_labels:
                    asset_data[r.asset][tf] = None
            if r.cont_rate is not None:
                asset_data[r.asset][r.timeframe] = r.cont_rate

        # Calculate averages
        for asset in asset_data.values():
            values = [
                asset[tf] for tf in all_tf_labels
                if asset.get(tf) is not None
            ]
            asset["avg"] = round(sum(values) / len(values), 1) if values else None

        return sorted(
            asset_data.values(), key=lambda x: (x["category"], x["asset"])
        )
