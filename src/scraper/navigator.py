"""
Chart navigation and CSV-based data extraction from TradingView.

Key design: loads the chart ONCE to preserve the indicator layout,
then changes symbol/timeframe using TradingView's UI controls
instead of reloading the page via URL.
"""

import csv
import glob
import logging
import os
import time
from typing import Optional, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

from ..config.assets import SCRAPER_CONFIG

logger = logging.getLogger(__name__)

# Timeframe label to TradingView UI text mapping
TF_INPUT_MAP = {
    "4H": "240",
    "1H": "60",
    "15min": "15",
    "5min": "5",
    "1min": "1",
}


class ChartNavigator:
    """Navigates TradingView charts and extracts data via CSV download.

    IMPORTANT: The chart is loaded once via initial_load(), preserving
    the user's layout with all indicators. Subsequent symbol/timeframe
    changes are done through TradingView's UI, not URL navigation.
    """

    def __init__(self, driver, download_dir: str = None):
        self.driver = driver
        self._current_symbol = None
        self._current_interval = None
        self._chart_loaded = False

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

    def initial_load(self) -> bool:
        """Load the chart page once to establish the layout with indicators.

        Must be called once after login, before any scanning.
        This loads the user's default layout which includes the SMC indicator.
        """
        try:
            logger.info("Loading initial chart page...")
            self.driver.get("https://www.tradingview.com/chart/")
            time.sleep(10)  # Wait for chart + indicators to fully load

            # Dismiss any popups
            self.dismiss_popups()

            self._chart_loaded = True
            logger.info("Chart page loaded with user's default layout")
            return True

        except Exception as e:
            logger.error(f"Failed to load initial chart: {e}")
            return False

    def navigate_to_chart(self, symbol: str, interval: str) -> bool:
        """Navigate to a specific symbol/timeframe WITHOUT reloading the page.

        Uses TradingView's UI controls to change symbol and timeframe,
        preserving the indicator layout.
        """
        if not self._chart_loaded:
            if not self.initial_load():
                return False

        try:
            # Change symbol if needed
            # Extract clean symbol name (e.g., "FX:USDJPY" -> "USDJPY" or keep full)
            if self._current_symbol != symbol:
                if not self._change_symbol(symbol):
                    logger.error(f"Failed to change symbol to {symbol}")
                    return False
                self._current_symbol = symbol
                time.sleep(3)  # Wait for new symbol data to load

            # Change timeframe if needed
            if self._current_interval != interval:
                if not self._change_timeframe(interval):
                    logger.error(f"Failed to change timeframe to {interval}")
                    return False
                self._current_interval = interval
                time.sleep(3)  # Wait for new timeframe data to load

            # Extra wait for indicators to recalculate
            time.sleep(SCRAPER_CONFIG.get("indicator_wait_timeout", 10))
            return True

        except Exception as e:
            logger.error(f"Navigation error for {symbol}@{interval}: {e}")
            return False

    def _change_symbol(self, symbol: str) -> bool:
        """Change the chart symbol using TradingView's symbol search.

        Opens the symbol search dialog, types the symbol, and selects it.
        """
        try:
            # Method 1: Click on the symbol name in the top-left
            # The symbol input/button is typically the first clickable element
            # in the chart header area
            try:
                symbol_btn = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "[data-name='legend-source-item'] [class*='title'], "
                    "[id='header-toolbar-symbol-search'], "
                    "[class*='symbolInput'], "
                    "[data-name='symbol-search-input']"
                )
                symbol_btn.click()
                time.sleep(1)
            except NoSuchElementException:
                # Method 2: Use keyboard shortcut to open symbol search
                ActionChains(self.driver).key_down(Keys.CONTROL).send_keys("k").key_up(Keys.CONTROL).perform()
                time.sleep(1)

            # Wait for search dialog/input to appear
            search_input = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "input[data-role='search'], "
                    "input[class*='search'], "
                    "input[placeholder*='Search'], "
                    "input[placeholder*='Symbol']"
                ))
            )

            # Clear and type the symbol
            search_input.clear()
            # Use the full symbol format (e.g., "FX:USDJPY")
            search_input.send_keys(symbol)
            time.sleep(2)  # Wait for search results

            # Press Enter to select the first result
            search_input.send_keys(Keys.ENTER)
            time.sleep(2)

            logger.info(f"Symbol changed to {symbol}")
            return True

        except Exception as e:
            logger.error(f"Failed to change symbol: {e}")
            # Fallback: try closing any open dialog
            try:
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            return False

    def _change_timeframe(self, interval: str) -> bool:
        """Change the chart timeframe using TradingView's timeframe input.

        Types the interval value directly in the timeframe input.
        """
        try:
            # Method 1: Click on the timeframe display and type the new value
            # The timeframe button/input is in the top toolbar
            try:
                tf_btn = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "[id='header-toolbar-intervals'] button[class*='isActive'], "
                    "[data-name='time-interval-menu'], "
                    "[class*='timeInterval']"
                )
                tf_btn.click()
                time.sleep(1)
            except NoSuchElementException:
                pass

            # Type the interval value - TradingView accepts typed numbers
            # for timeframe change when chart is focused
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys(interval)
            time.sleep(0.5)
            body.send_keys(Keys.ENTER)
            time.sleep(1)

            logger.info(f"Timeframe changed to {interval}")
            return True

        except Exception as e:
            logger.error(f"Failed to change timeframe: {e}")
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
        except (NoSuchElementException, Exception):
            pass

        # Close any open dialogs
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.3)
        except Exception:
            pass

    def get_cont_rate_from_csv(
        self, asset_name: str = "", timeframe: str = "",
        max_download_wait: int = 30
    ) -> Tuple[Optional[float], float]:
        """Extract Continuation Rate by downloading chart data as CSV.

        Flow:
        1. Open the save/load dropdown menu (top right near "Save")
        2. Click "Download chart data..."
        3. Click "Download" in the dialog
        4. Parse CSV: last row of "Continuation Rate" column
        5. Clean up downloaded file

        Returns:
            Tuple of (cont_rate, confidence).
            confidence is 1.0 for CSV extraction (always accurate).
        """
        csv_path = None
        try:
            # Clean up any previous CSV files
            self._clean_downloads()

            # Step 1: Open the dropdown menu that contains "Download chart data"
            if not self._open_save_menu():
                logger.warning("Could not open save/load menu")
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

    def _open_save_menu(self) -> bool:
        """Open the save/load dropdown menu that contains 'Download chart data'.

        This is the dropdown near the 'Save' button in the top-right toolbar.
        """
        try:
            # Look for the dropdown arrow/chevron next to the Save button
            selectors = [
                # The small dropdown arrow next to "Save"
                "[id='header-toolbar-save-load'] button:last-child",
                "[id='header-toolbar-save-load'] [class*='arrow']",
                "[id='header-toolbar-save-load'] [class*='dropdown']",
                # The "Save" text with dropdown
                "button[aria-label*='Save']",
                "[data-name='save-load-menu']",
                # More generic: look for save area
                "[class*='saveLoad'] button",
            ]

            for selector in selectors:
                try:
                    btns = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in btns:
                        try:
                            if btn.is_displayed():
                                btn.click()
                                time.sleep(1)

                                # Check if "Download chart data" appeared
                                try:
                                    self.driver.find_element(
                                        By.XPATH,
                                        "//*[contains(text(), 'Download chart data')]"
                                    )
                                    logger.info(f"Save menu opened with: {selector}")
                                    return True
                                except NoSuchElementException:
                                    # Wrong menu, close it
                                    ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                                    time.sleep(0.3)
                        except Exception:
                            continue
                except Exception:
                    continue

            # Fallback: try clicking the chevron/arrow icon near "Save" text
            try:
                save_elements = self.driver.find_elements(
                    By.XPATH, "//*[text()='Save']/parent::*/following-sibling::*"
                )
                for el in save_elements:
                    try:
                        el.click()
                        time.sleep(1)
                        self.driver.find_element(
                            By.XPATH,
                            "//*[contains(text(), 'Download chart data')]"
                        )
                        logger.info("Save menu opened via Save sibling")
                        return True
                    except Exception:
                        continue
            except Exception:
                pass

            # Last resort: find the dropdown near top-right area
            try:
                # Look for any dropdown trigger that reveals "Download chart data"
                all_buttons = self.driver.find_elements(
                    By.CSS_SELECTOR, "header button, [class*='toolbar'] button"
                )
                for btn in all_buttons:
                    try:
                        if not btn.is_displayed() or btn.size['width'] < 5:
                            continue
                        # Check if it's in the right area of the page
                        location = btn.location
                        if location['x'] > 800:  # Right side of screen
                            btn.click()
                            time.sleep(0.5)
                            try:
                                self.driver.find_element(
                                    By.XPATH,
                                    "//*[contains(text(), 'Download chart data')]"
                                )
                                logger.info("Save menu opened via toolbar scan")
                                return True
                            except NoSuchElementException:
                                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                                time.sleep(0.2)
                    except Exception:
                        continue
            except Exception:
                pass

            logger.warning("All save menu selectors failed")
            return False

        except Exception as e:
            logger.error(f"Error opening save menu: {e}")
            return False

    def _click_download_chart_data(self) -> bool:
        """Click the 'Download chart data...' option in the dropdown menu."""
        try:
            download_option = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//*[contains(text(), 'Download chart data')]"
                ))
            )
            download_option.click()
            time.sleep(2)  # Wait for dialog to appear
            logger.info("'Download chart data' clicked")
            return True

        except (TimeoutException, NoSuchElementException) as e:
            logger.warning(f"Could not find 'Download chart data': {e}")
            return False

    def _click_download_button(self) -> bool:
        """Click the 'Download' button in the download dialog."""
        try:
            # Wait for dialog to be fully rendered
            time.sleep(1)

            # Try multiple strategies to find the Download button

            # Strategy 1: Find button by exact text "Download" within dialog
            try:
                buttons = self.driver.find_elements(By.TAG_NAME, "button")
                for btn in buttons:
                    try:
                        btn_text = btn.text.strip()
                        if btn_text == "Download":
                            btn.click()
                            logger.info("Download button clicked")
                            time.sleep(2)
                            return True
                    except Exception:
                        continue
            except Exception:
                pass

            # Strategy 2: XPath with various patterns
            xpaths = [
                "//button[text()='Download']",
                "//button[normalize-space()='Download']",
                "//button[contains(@class, 'primary') or contains(@class, 'submit')]",
                "//div[contains(@class, 'dialog')]//button[last()]",
            ]
            for xpath in xpaths:
                try:
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    if "Download" in btn.text or "dialog" in xpath:
                        btn.click()
                        logger.info(f"Download button clicked via: {xpath}")
                        time.sleep(2)
                        return True
                except (TimeoutException, NoSuchElementException):
                    continue

            # Strategy 3: Use JavaScript to click
            try:
                self.driver.execute_script("""
                    var buttons = document.querySelectorAll('button');
                    for (var btn of buttons) {
                        if (btn.textContent.trim() === 'Download' && 
                            btn.offsetParent !== null) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                """)
                logger.info("Download button clicked via JavaScript")
                time.sleep(2)
                return True
            except Exception:
                pass

            logger.warning("Could not find Download button in dialog")
            return False

        except Exception as e:
            logger.error(f"Error clicking Download button: {e}")
            return False

    def _wait_for_download(self, max_wait: int = 30) -> Optional[str]:
        """Wait for a CSV file to appear in the download directory."""
        start = time.time()
        while time.time() - start < max_wait:
            csv_files = glob.glob(os.path.join(self._download_dir, "*.csv"))
            partial = glob.glob(os.path.join(self._download_dir, "*.crdownload"))

            if csv_files and not partial:
                newest = max(csv_files, key=os.path.getmtime)
                logger.info(f"CSV downloaded: {newest}")
                return newest

            time.sleep(1)

        return None

    def _parse_csv_cont_rate(self, csv_path: str) -> Optional[float]:
        """Parse the downloaded CSV and extract the last Continuation Rate value."""
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            if not rows:
                logger.warning("CSV file is empty")
                return None

            # Find the Continuation Rate column
            cont_rate_col = None
            for col_name in rows[0].keys():
                if "continuation" in col_name.lower() and "rate" in col_name.lower():
                    cont_rate_col = col_name
                    break

            if not cont_rate_col:
                available = list(rows[0].keys())
                logger.warning(
                    f"No 'Continuation Rate' column found. "
                    f"Available columns: {available}"
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
                            logger.warning(f"Cont Rate {cont_rate} outside 0-100")
                            return None
                    except ValueError:
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
            logger.info(f"Current URL: {self.driver.current_url}")

        except Exception as e:
            logger.warning(f"Could not save debug screenshot: {e}")
