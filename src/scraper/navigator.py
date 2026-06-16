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

    def navigate_to_chart(self, symbol: str, interval: str, indicator_wait: int = 4) -> bool:
        """Navigate to a specific symbol/timeframe WITHOUT reloading the page.

        Uses TradingView's UI controls to change symbol and timeframe,
        preserving the indicator layout.
        
        Args:
            indicator_wait: Seconds to wait for indicators to calculate.
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
                # _change_symbol fa un reload via URL navigation, che RESETTA il
                # timeframe al default del layout. Invalidiamo _current_interval
                # per forzare il re-set del timeframe sotto, anche se l'interval
                # richiesto coincide con quello precedente.
                self._current_interval = None
                time.sleep(2)  # Wait for new symbol data

            # Change timeframe if needed
            if self._current_interval != interval:
                if not self._change_timeframe(interval):
                    logger.error(f"Failed to change timeframe to {interval}")
                    return False
                self._current_interval = interval
                time.sleep(2)  # Wait for new timeframe data

            # Wait for indicators to recalculate
            time.sleep(indicator_wait)
            return True

        except Exception as e:
            logger.error(f"Navigation error for {symbol}@{interval}: {e}")
            return False

    def _dismiss_overlays(self) -> None:
        """Dismiss eventuali popup/modal promozionali TradingView (es. Summer Sale,
        toast "claim it now") che intercettano i click sull'header toolbar.

        Strategia in 3 layer:
          1. Click sulla X di chiusura standard (selettori multipli noti).
          2. ESC su body — chiude la maggior parte dei dialog HTML.
          3. JS removal forzata di pattern noti come fallback finale.

        Non solleva mai. Va chiamato all'inizio di ogni interazione con l'header.
        """
        # Layer 1: click su X di chiusura — selettori multipli per popup TV diversi
        close_selectors = [
            "button[data-name='close']",
            "button[aria-label='Close']",
            "button[aria-label='close']",
            "[class*='closeButton'] button",
            "[class*='close-button']",
            "div[role='dialog'] button[class*='close']",
            "div[data-dialog-name] button[class*='close']",
        ]
        for sel in close_selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    if el.is_displayed():
                        try:
                            self.driver.execute_script("arguments[0].click();", el)
                            time.sleep(0.3)
                        except Exception:
                            pass
            except Exception:
                pass

        # Layer 2: ESC su body (chiude modal residui che catturano keyboard input)
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.3)
        except Exception:
            pass

        # Layer 3: JS removal di pattern noti — promo modal full-page + toast
        # bottom-left "Summer sale awaits / Explore offers" che persistono dopo l'ESC.
        try:
            self.driver.execute_script(
                """
                // Modal promo full-page (Summer Sale, Black Friday, ecc.)
                document.querySelectorAll('[class*="dialog-"][class*="promotional"], '
                    + '[data-dialog-name*="promo"], '
                    + 'div[class*="dialog-wrapper"] div[class*="dialog-"]:not([data-dialog-name="symbol-search"])'
                ).forEach(function(el) {
                    var txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('sale') || txt.includes('off') || txt.includes('claim') || txt.includes('offer')) {
                        el.remove();
                    }
                });
                // Toast notifications bottom-left
                document.querySelectorAll('[class*="toast"], [class*="snackbar"], [class*="notification-banner"]').forEach(function(el) {
                    var txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('sale') || txt.includes('offer') || txt.includes('claim')) {
                        el.remove();
                    }
                });
                // Backdrop overlay che intercetta i click
                document.querySelectorAll('[class*="backdrop"], [class*="overlay-wrap"]').forEach(function(el) {
                    if (el.style.position === 'fixed' || getComputedStyle(el).position === 'fixed') {
                        // Solo se non è il dialog di symbol search
                        if (!el.querySelector('input[data-role="search"]')) {
                            el.remove();
                        }
                    }
                });
                """
            )
            time.sleep(0.2)
        except Exception:
            pass

    def _current_chart_symbol(self) -> str:
        """Estrae il TICKER attualmente caricato sul chart.

        Fonte primaria: document.title. TradingView aggiorna il <title> del tab
        col ticker + prezzo correnti (es. 'USDJPY 156.42 ▲ ...') ad ogni cambio
        simbolo, ANCHE quando si usa un layout salvato (in cui l'URL resta pulito
        tipo /chart/KKDLn4WZ/ e NON contiene ?symbol=). Per questo il title è più
        affidabile dell'URL come segnale di "simbolo davvero cambiato".

        Fallback: query param 'symbol' nell'URL (per chart senza layout salvato).

        Ritorna il primo token (il ticker, es. 'USDJPY'), uppercase. '' se ignoto.
        """
        # Fonte 1: document.title (robusta col layout salvato)
        try:
            title = self.driver.title or ""
            # Formato tipico: "USDJPY 156.420 ▲ +0.21% ..." → primo token = ticker
            token = title.strip().split(" ", 1)[0].split(":")[-1].upper()
            # Filtra titoli non-chart (es. "TradingView" durante il loading)
            if token and token.upper() != "TRADINGVIEW" and any(c.isalpha() for c in token):
                return token
        except Exception:
            pass

        # Fonte 2: URL query param (chart senza layout salvato)
        try:
            url = self.driver.current_url or ""
            if "symbol=" in url:
                part = url.split("symbol=", 1)[1].split("&", 1)[0]
                return part.replace("%3A", ":").split(":")[-1].upper()
        except Exception:
            pass
        return ""

    def _change_symbol(self, symbol: str) -> bool:
        """Cambia il simbolo del chart via URL navigation DIRETTA (deterministica).

        Perché URL navigation invece del symbol search UI:
        1. DETERMINISTICA: ?symbol=OANDA:USDJPY carica ESATTAMENTE quel broker.
           Il symbol search UI invece seleziona il primo risultato della dropdown,
           che potrebbe essere un broker diverso (es. FXCM invece di OANDA) e
           quindi prezzi/continuation rate diversi da quelli del broker target.
        2. AFFIDABILE: il symbol search UI fallisce silenziosamente coi layout
           salvati (digiti il simbolo ma il chart non cambia).

        Il layout (indicatore SMC) viene PRESERVATO perché navighiamo sull'URL
        del chart corrente mantenendo il layout ID (es. /chart/KKDLn4WZ/), che
        TradingView ricarica con tutti gli indicatori salvati sul nuovo simbolo.
        """
        # Dismiss popup promo prima (non serve per la nav, ma pulisce lo stato)
        self._dismiss_overlays()

        target_token = symbol.split(":")[-1].upper()

        try:
            # base = URL del chart corrente SENZA query params. Dopo initial_load
            # TradingView redirige a /chart/<LAYOUT_ID>/ — mantenendo l'ID qui
            # preserviamo il layout con gli indicatori. Senza ID (/chart/) si
            # caricherebbe comunque il default layout dell'utente.
            base = self.driver.current_url.split("?")[0]
            if "/chart/" not in base:
                base = "https://www.tradingview.com/chart/"

            self.driver.get(f"{base}?symbol={symbol}")

            # Verifica via document.title che il nuovo ticker sia caricato.
            # Polling attivo (0.5s, max 12s) per coprire il reload completo del
            # layout + ricalcolo indicatori, uscendo appena il title conferma.
            for _ in range(24):  # 24 × 0.5s = 12s max
                if target_token in self._current_chart_symbol():
                    # Dismiss eventuali popup ricomparsi dopo il reload pagina
                    self._dismiss_overlays()
                    logger.info(
                        f"Symbol changed to {symbol} (title ticker: {self._current_chart_symbol()})"
                    )
                    return True
                time.sleep(0.5)

            logger.error(
                f"Symbol change FAILED: target={symbol}, "
                f"title='{(self.driver.title or '')[:60]}'"
            )
            return False

        except Exception as e:
            logger.error(f"Failed to change symbol to {symbol}: {e}")
            return False

    def _change_timeframe(self, interval: str) -> bool:
        """Change the chart timeframe by typing the interval number.

        TradingView accepts typed numbers when the chart has focus.
        """
        # Dismiss popup promo prima del click sul canvas: senza focus sul chart,
        # i send_keys successivi vanno persi nel popup invece di trigger il
        # timeframe input di TradingView.
        self._dismiss_overlays()

        try:
            # Click on the chart area first to ensure it has focus
            try:
                chart = self.driver.find_element(
                    By.CSS_SELECTOR, "canvas, [class*='chart']"
                )
                chart.click()
                time.sleep(0.5)
            except Exception:
                pass

            # Type the interval value and press Enter
            actions = ActionChains(self.driver)
            actions.send_keys(interval)
            actions.perform()
            time.sleep(0.5)

            actions = ActionChains(self.driver)
            actions.send_keys(Keys.ENTER)
            actions.perform()
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
    ) -> Tuple[Optional[float], float, Optional[int], Optional[int]]:
        """Extract Continuation Rate, Mode, and Status by downloading chart data as CSV.

        Flow:
        1. Open the save/load dropdown menu (top right near "Save")
        2. Click "Download chart data..."
        3. Click "Download" in the dialog
        4. Parse CSV: last row of "Continuation Rate", "Mode", "Status" columns
        5. Clean up downloaded file

        Returns:
            Tuple of (cont_rate, confidence, mode, status_code).
            confidence is 1.0 for CSV extraction (always accurate).
            mode: 1=Uptrend, -1=Downtrend, None if not found.
            status_code: 0-8, None if not found.
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

            # Step 5: Parse CSV and extract Continuation Rate + Mode + Status
            cont_rate, mode, status_code = self._parse_csv_data(csv_path)
            if cont_rate is not None:
                logger.info(
                    f"CSV extraction: {asset_name}@{timeframe} "
                    f"Continuation Rate = {cont_rate}, Mode = {mode}, Status = {status_code}"
                )
                return cont_rate, 1.0, mode, status_code
            else:
                logger.warning(
                    f"Could not find Continuation Rate in CSV "
                    f"for {asset_name}@{timeframe}"
                )
                return None, 0.0, mode, status_code

        except Exception as e:
            logger.error(f"CSV extraction error: {e}")
            self._save_debug_screenshot(asset_name, timeframe)
            return None, 0.0, None, None

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

    def _parse_csv_data(self, csv_path: str) -> Tuple[Optional[float], Optional[int], Optional[int]]:
        """Parse the downloaded CSV and extract Continuation Rate, Mode, and Status.

        Returns:
            Tuple of (cont_rate, mode, status_code).
        """
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            if not rows:
                logger.warning("CSV file is empty")
                return None, None, None

            # Find columns
            cont_rate_col = None
            mode_col = None
            status_col = None
            for col_name in rows[0].keys():
                col_lower = col_name.lower().strip()
                if "continuation" in col_lower and "rate" in col_lower:
                    cont_rate_col = col_name
                elif col_lower == "mode":
                    mode_col = col_name
                elif col_lower == "status":
                    status_col = col_name

            if not cont_rate_col:
                available = list(rows[0].keys())
                logger.warning(
                    f"No 'Continuation Rate' column found. "
                    f"Available columns: {available}"
                )
                return None, None, None

            # Extract Continuation Rate: last non-empty value (usually only last row)
            cont_rate = None
            for row in reversed(rows):
                value = row.get(cont_rate_col, "").strip()
                if value and value.lower() not in ("", "nan", "n/a", "null"):
                    try:
                        cr = float(value)
                        if 0 <= cr <= 100:
                            cont_rate = round(cr, 1)
                            break
                        else:
                            logger.warning(f"Cont Rate {cr} outside 0-100")
                    except ValueError:
                        continue

            # Extract Mode: last non-empty value
            mode = None
            if mode_col:
                for row in reversed(rows):
                    value = row.get(mode_col, "").strip()
                    if value and value.lower() not in ("", "nan", "n/a", "null"):
                        try:
                            mode = int(float(value))
                            break
                        except ValueError:
                            continue

            # Extract Status: last non-empty value
            status_code = None
            if status_col:
                for row in reversed(rows):
                    value = row.get(status_col, "").strip()
                    if value and value.lower() not in ("", "nan", "n/a", "null"):
                        try:
                            status_code = int(float(value))
                            break
                        except ValueError:
                            continue

            if cont_rate is None:
                logger.warning("No valid Continuation Rate value found in CSV")

            logger.debug(f"CSV parsed: CR={cont_rate}, Mode={mode}, Status={status_code}")
            return cont_rate, mode, status_code

        except Exception as e:
            logger.error(f"Error parsing CSV: {e}")
            return None, None, None

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
