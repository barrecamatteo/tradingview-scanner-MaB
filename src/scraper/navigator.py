"""
Chart navigation and CSV-based data extraction from TradingView.

Extracts Continuation Rate by downloading chart data as CSV,
which includes all indicator values. The last row of the
"Continuation Rate" column contains the current value.
"""

import csv
import glob
import logging
import os
import time
from typing import Optional, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

from ..config.assets import TV_CHART_URL, SCRAPER_CONFIG

logger = logging.getLogger(__name__)


class ChartNavigator:
    """Navigates TradingView charts and extracts data via CSV download."""

    def __init__(self, driver, download_dir: str = None):
        self.driver = driver
        self._current_symbol = None
        self._current_interval = None

        # Set download directory
        if download_dir is None:
            self._download_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)
                ))),
                "data", "downloads"
            )
        else:
            self._download_dir = download_dir
        os.makedirs(self._download_dir, exist_ok=True)

    def navigate_to_chart(self, symbol: str, interval: str) -> bool:
        """Navigate to a specific symbol/timeframe chart.

        Returns True if navigation was successful.
        """
        try:
            url = TV_CHART_URL.format(symbol=symbol, interval=interval)

            # Page load can be slow with many indicators - handle timeout gracefully
            try:
                self.driver.get(url)
            except TimeoutException:
                logger.warning(f"Page load timeout for {symbol}@{interval}, continuing anyway...")

            self._current_symbol = symbol
            self._current_interval = interval

            # Wait for chart to load (increase wait time for heavy charts)
            timeout = 60  # 60 seconds for chart with many indicators
            try:
                WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "[class*='chart'], canvas, [class*='layout']")
                    )
                )
            except TimeoutException:
                logger.warning(f"Chart element not found for {symbol}@{interval}, continuing...")

            # Wait for indicators to calculate
            time.sleep(SCRAPER_CONFIG["indicator_wait_timeout"])
            return True

        except TimeoutException:
            logger.error(f"Timeout loading chart for {symbol}@{interval}")
            return False
        except Exception as e:
            logger.error(f"Navigation error for {symbol}@{interval}: {e}")
            return False

    def dismiss_popups(self):
        """Dismiss any TradingView popups, cookie banners, or dialogs."""
        # Cookie banner
        try:
            accept_btn = self.driver.find_element(
                By.XPATH, "//button[contains(text(), 'Accept all')]"
            )
            accept_btn.click()
            time.sleep(0.5)
            logger.info("Cookie banner dismissed")
        except NoSuchElementException:
            pass
        except Exception:
            pass

        # Generic popups / close buttons
        popup_selectors = [
            "[data-role='toast-container'] button",
            "[class*='close']",
            "[class*='dialog'] button[class*='close']",
        ]
        for selector in popup_selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    try:
                        el.click()
                        time.sleep(0.3)
                    except Exception:
                        pass
            except Exception:
                pass

    def get_cont_rate_from_csv(
        self, asset_name: str = "", timeframe: str = "",
        max_download_wait: int = 30
    ) -> Tuple[Optional[float], float]:
        """Extract Continuation Rate by downloading chart data as CSV.

        Flow:
        1. Open the layout dropdown menu (top right)
        2. Click "Download chart data..."
        3. Click "Download" in the dialog
        4. Parse CSV: last row of "Continuation Rate" column
        5. Clean up downloaded file

        Args:
            asset_name: For logging.
            timeframe: For logging.
            max_download_wait: Max seconds to wait for CSV download.

        Returns:
            Tuple of (cont_rate, confidence).
            confidence is 1.0 for CSV extraction (always accurate).
        """
        csv_path = None
        try:
            # Clean up any previous CSV files in download dir
            self._clean_downloads()

            # Step 1: Open the layout dropdown menu
            if not self._open_layout_menu():
                logger.warning("Could not open layout dropdown menu")
                self._save_debug_screenshot(asset_name, timeframe)
                return None, 0.0

            # Step 2: Click "Download chart data..."
            if not self._click_download_chart_data():
                logger.warning("Could not find 'Download chart data' option")
                self._save_debug_screenshot(asset_name, timeframe)
                return None, 0.0

            # Step 3: Click "Download" button in the dialog
            if not self._click_download_button():
                logger.warning("Could not click Download button in dialog")
                self._save_debug_screenshot(asset_name, timeframe)
                return None, 0.0

            # Step 4: Wait for CSV file to appear
            csv_path = self._wait_for_download(max_download_wait)
            if not csv_path:
                logger.warning(
                    f"CSV download did not complete within {max_download_wait}s"
                )
                self._save_debug_screenshot(asset_name, timeframe)
                return None, 0.0

            # Step 5: Parse CSV and extract Continuation Rate
            cont_rate = self._parse_csv_cont_rate(csv_path)
            if cont_rate is not None:
                logger.info(
                    f"CSV extraction: {asset_name}@{timeframe} "
                    f"Continuation Rate = {cont_rate}"
                )
                return cont_rate, 1.0
            else:
                logger.warning(
                    f"Could not find Continuation Rate in CSV "
                    f"for {asset_name}@{timeframe}"
                )
                return None, 0.0

        except Exception as e:
            logger.error(f"CSV extraction error: {e}")
            self._save_debug_screenshot(asset_name, timeframe)
            return None, 0.0

        finally:
            # Clean up downloaded CSV
            if csv_path and os.path.exists(csv_path):
                try:
                    os.remove(csv_path)
                except Exception:
                    pass

    def _open_layout_menu(self) -> bool:
        """Open the layout dropdown menu (top right area)."""
        try:
            # Try multiple selectors for the layout menu button
            selectors = [
                # The dropdown button near "Senza nome" / layout name
                "[data-name='save-load-menu']",
                "[class*='saveLoad']",
                "[aria-label*='Save']",
                # Generic: button that contains the layout name area
                "button[class*='menu']",
            ]

            for selector in selectors:
                try:
                    btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable(
                            (By.CSS_SELECTOR, selector)
                        )
                    )
                    btn.click()
                    time.sleep(1)
                    
                    # Verify menu opened by checking for "Download chart data"
                    try:
                        self.driver.find_element(
                            By.XPATH,
                            "//*[contains(text(), 'Download chart data')]"
                        )
                        logger.debug(f"Layout menu opened with selector: {selector}")
                        return True
                    except NoSuchElementException:
                        # Menu opened but wrong menu, close and try next
                        try:
                            self.driver.find_element(By.TAG_NAME, "body").click()
                            time.sleep(0.3)
                        except Exception:
                            pass
                        continue

                except (TimeoutException, NoSuchElementException):
                    continue

            # Fallback: try clicking the layout name text directly
            try:
                layout_elements = self.driver.find_elements(
                    By.XPATH,
                    "//*[contains(@class, 'layoutName') or "
                    "contains(@class, 'title') and "
                    "contains(@class, 'save')]"
                )
                for el in layout_elements:
                    try:
                        el.click()
                        time.sleep(1)
                        # Check if download option appeared
                        self.driver.find_element(
                            By.XPATH,
                            "//*[contains(text(), 'Download chart data')]"
                        )
                        return True
                    except Exception:
                        continue
            except Exception:
                pass

            # Last resort: try keyboard shortcut or direct URL approach
            logger.warning("All layout menu selectors failed")
            return False

        except Exception as e:
            logger.error(f"Error opening layout menu: {e}")
            return False

    def _click_download_chart_data(self) -> bool:
        """Click the 'Download chart data...' option in the dropdown menu."""
        try:
            # Find and click the "Download chart data..." text
            download_option = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//*[contains(text(), 'Download chart data')]"
                ))
            )
            download_option.click()
            time.sleep(1)

            # Verify the download dialog opened
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        "//div[contains(text(), 'Download chart data') and "
                        "contains(@class, 'title')]"
                        " | "
                        "//div[contains(@class, 'dialog')]"
                        "//button[contains(text(), 'Download')]"
                    ))
                )
            except TimeoutException:
                pass  # Dialog might have different structure

            logger.debug("'Download chart data' option clicked")
            return True

        except (TimeoutException, NoSuchElementException) as e:
            logger.warning(f"Could not find 'Download chart data': {e}")
            return False

    def _click_download_button(self) -> bool:
        """Click the 'Download' button in the download dialog."""
        try:
            # Look for the Download button in the dialog
            # It's distinct from "Download chart data" - it's just "Download"
            download_btn = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[normalize-space(text())='Download' and "
                    "not(contains(text(), 'chart'))]"
                ))
            )
            download_btn.click()
            logger.debug("Download button clicked")
            time.sleep(2)
            return True

        except (TimeoutException, NoSuchElementException):
            # Fallback: try any button with "Download" text
            try:
                buttons = self.driver.find_elements(
                    By.XPATH, "//button[contains(text(), 'Download')]"
                )
                # Click the last one (typically the dialog button, not the menu)
                for btn in reversed(buttons):
                    try:
                        if btn.is_displayed() and btn.is_enabled():
                            btn.click()
                            time.sleep(2)
                            return True
                    except Exception:
                        continue
            except Exception:
                pass

            logger.warning("Could not find Download button in dialog")
            return False

    def _wait_for_download(self, max_wait: int = 30) -> Optional[str]:
        """Wait for a CSV file to appear in the download directory.

        Returns the path to the downloaded CSV file, or None on timeout.
        """
        start = time.time()
        while time.time() - start < max_wait:
            # Look for CSV files (TradingView names them like OANDA_USDJPY, 1.csv)
            csv_files = glob.glob(os.path.join(self._download_dir, "*.csv"))

            # Filter out any .crdownload (partial Chrome downloads)
            partial = glob.glob(os.path.join(self._download_dir, "*.crdownload"))

            if csv_files and not partial:
                # Return the most recently modified CSV
                newest = max(csv_files, key=os.path.getmtime)
                logger.debug(f"CSV downloaded: {newest}")
                return newest

            time.sleep(1)

        return None

    def _parse_csv_cont_rate(self, csv_path: str) -> Optional[float]:
        """Parse the downloaded CSV and extract the last Continuation Rate value.

        The CSV has columns like: time, open, high, low, close,
        Continuation Rate, Basis, Upper, Lower, RSI, ...

        The last row contains the most recent (current) values.
        """
        try:
            # Read the CSV
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            if not rows:
                logger.warning("CSV file is empty")
                return None

            # Find the Continuation Rate column
            # Column name might vary slightly
            cont_rate_col = None
            for col_name in rows[0].keys():
                if "continuation" in col_name.lower() and "rate" in col_name.lower():
                    cont_rate_col = col_name
                    break

            if not cont_rate_col:
                logger.warning(
                    f"No 'Continuation Rate' column found. "
                    f"Available columns: {list(rows[0].keys())}"
                )
                return None

            # Get the last non-empty value (scan from bottom up)
            for row in reversed(rows):
                value = row.get(cont_rate_col, "").strip()
                if value and value.lower() not in ("", "nan", "n/a", "null"):
                    try:
                        cont_rate = float(value)
                        if 0 <= cont_rate <= 100:
                            return round(cont_rate, 1)
                        else:
                            logger.warning(
                                f"Continuation Rate {cont_rate} outside 0-100"
                            )
                            return None
                    except ValueError:
                        logger.warning(f"Cannot parse '{value}' as float")
                        continue

            logger.warning("No valid Continuation Rate value found in CSV")
            return None

        except Exception as e:
            logger.error(f"Error parsing CSV: {e}")
            return None

    def _clean_downloads(self):
        """Remove any existing CSV files from the download directory."""
        try:
            for f in glob.glob(os.path.join(self._download_dir, "*.csv")):
                os.remove(f)
            for f in glob.glob(os.path.join(self._download_dir, "*.crdownload")):
                os.remove(f)
        except Exception as e:
            logger.warning(f"Error cleaning downloads: {e}")

    def _save_debug_screenshot(self, asset_name: str, timeframe: str):
        """Save a debug screenshot on extraction failure."""
        try:
            debug_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)
                ))),
                "data", "screenshots"
            )
            os.makedirs(debug_dir, exist_ok=True)

            debug_name = f"debug_{asset_name}_{timeframe}".replace(" ", "_")
            screenshot_path = os.path.join(debug_dir, f"{debug_name}.png")
            self.driver.save_screenshot(screenshot_path)
            logger.info(f"Debug screenshot saved: {screenshot_path}")

            # Log current URL for debugging
            logger.info(f"Current URL: {self.driver.current_url}")

        except Exception as e:
            logger.warning(f"Could not save debug screenshot: {e}")
