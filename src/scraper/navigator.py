"""
Navigator: Handles chart navigation between assets and timeframes on TradingView.
"""

import time
import random
import logging
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from ..config.assets import TV_CHART_URL, SCRAPER_CONFIG

logger = logging.getLogger(__name__)


class ChartNavigator:
    """Navigates TradingView charts across different assets and timeframes."""

    def __init__(self, driver):
        self.driver = driver
        self.config = SCRAPER_CONFIG

    def navigate_to_chart(self, symbol: str, interval: str) -> bool:
        """
        Navigate to a specific asset/timeframe combination.
        """
        url = TV_CHART_URL.format(symbol=symbol, interval=interval)
        logger.info(f"Navigating to {symbol} @ {interval}min")

        try:
            self.driver.get(url)
            time.sleep(2)

            # Wait for chart to load
            if not self._wait_for_chart_load():
                logger.warning(f"Chart load timeout for {symbol} @ {interval}")
                return False

            # Close right sidebar panels (Watchlist, etc.)
            self._close_right_panels()

            # Hide all manual drawings on chart
            self._hide_drawings()

            # Dismiss popups
            self.dismiss_popups()

            # Wait for indicator to compute
            time.sleep(3)

            # Scroll chart to the RIGHT so candles move left,
            # leaving clean empty space under the Analysis panel
            self._scroll_chart_right()

            # Wait for rendering to settle
            if not self._wait_for_indicator_panel():
                logger.warning(f"Indicator panel not found for {symbol} @ {interval}")
                return False

            # Random delay to avoid rate limiting
            delay = random.uniform(
                self.config["delay_between_requests_min"],
                self.config["delay_between_requests_max"],
            )
            time.sleep(delay)

            return True

        except TimeoutException:
            logger.error(f"Timeout navigating to {symbol} @ {interval}")
            return False
        except Exception as e:
            logger.error(f"Navigation error for {symbol} @ {interval}: {e}")
            return False

    def _wait_for_chart_load(self) -> bool:
        """Wait for the TradingView chart canvas to render."""
        try:
            wait = WebDriverWait(self.driver, self.config["page_load_timeout"])
            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "canvas, .chart-container"))
            )
            time.sleep(3)
            return True
        except TimeoutException:
            return False

    def _scroll_chart_right(self):
        """
        Scroll the chart forward in time (RIGHT arrow) so that all candles
        move off-screen to the left, leaving the right side of the chart
        (where the Analysis panel is) with a clean empty background.
        This makes OCR much more reliable.
        """
        try:
            # Click on the chart area first to make sure it has focus
            try:
                chart_area = self.driver.find_element(By.CSS_SELECTOR, ".chart-container, .chart-markup-table")
                chart_area.click()
                time.sleep(0.3)
            except Exception:
                body = self.driver.find_element(By.TAG_NAME, "body")
                actions = ActionChains(self.driver)
                actions.move_to_element(body).click().perform()
                time.sleep(0.3)

            # Press Right arrow many times to scroll chart forward in time
            # This pushes candles to the left, leaving empty space on the right
            body = self.driver.find_element(By.TAG_NAME, "body")
            for _ in range(150):
                body.send_keys(Keys.ARROW_RIGHT)
                time.sleep(0.02)

            time.sleep(1)
            logger.info("Chart scrolled right - candles moved left for clean Analysis panel")

        except Exception as e:
            logger.warning(f"Could not scroll chart right: {e}")

    def _hide_drawings(self):
        """
        Click the 'eye' button on the left toolbar to hide all manual drawings.
        This prevents drawn objects from overlapping the Analysis panel.
        The button toggles visibility of all drawing objects on the chart.
        """
        try:
            # Try multiple selectors for the hide/show drawings button
            drawing_visibility_selectors = [
                # Eye icon button in left toolbar
                "[data-name='drawingToolbarToggleVisibility']",
                "[data-name='toggleVisibilityOfSelectedDrawings']",
                "[data-name='hideAllDrawingTools']",
                # Generic eye icon in drawing toolbar
                ".drawingToolbar button[data-name*='isibility']",
                ".drawingToolbar button[data-name*='eye']",
                # Left toolbar buttons with visibility/eye related attributes
                "[id*='drawing'] [data-name*='isibility']",
            ]

            for selector in drawing_visibility_selectors:
                try:
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        try:
                            btn.click()
                            time.sleep(0.5)
                            logger.info(f"Toggled drawing visibility via: {selector}")
                            return
                        except Exception:
                            continue
                except Exception:
                    continue

            # Fallback: try finding the eye icon by its SVG path or aria-label
            try:
                eye_buttons = self.driver.find_elements(
                    By.XPATH,
                    "//button[contains(@aria-label, 'isib') or contains(@aria-label, 'Hide') "
                    "or contains(@aria-label, 'Nascondi') or contains(@aria-label, 'drawing')]"
                )
                for btn in eye_buttons:
                    try:
                        # Only click buttons in the left toolbar area
                        location = btn.location
                        if location["x"] < 100:  # Left toolbar is on the far left
                            btn.click()
                            time.sleep(0.5)
                            logger.info("Toggled drawing visibility via aria-label")
                            return
                    except Exception:
                        continue
            except Exception:
                pass

            # Last resort: use keyboard shortcut Ctrl+Shift+H (TradingView shortcut)
            try:
                body = self.driver.find_element(By.TAG_NAME, "body")
                from selenium.webdriver.common.action_chains import ActionChains
                actions = ActionChains(self.driver)
                actions.key_down(Keys.CONTROL).key_down(Keys.SHIFT).send_keys("h").key_up(Keys.SHIFT).key_up(Keys.CONTROL).perform()
                time.sleep(0.5)
                logger.info("Toggled drawing visibility via Ctrl+Shift+H shortcut")
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"Could not hide drawings: {e}")

    def _close_right_panels(self):
        """
        Close all right sidebar panels (Watchlist, Details, etc.)
        """
        try:
            right_panel_selectors = [
                "[data-name='right-toolbar'] button[aria-pressed='true']",
                "button[data-name='watchlists'][aria-pressed='true']",
                "button[data-name='data-window'][aria-pressed='true']",
                "button[data-name='object-tree-and-data-window'][aria-pressed='true']",
                "button[data-name='news'][aria-pressed='true']",
                "button[data-name='hotlists'][aria-pressed='true']",
            ]

            for selector in right_panel_selectors:
                try:
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in buttons:
                        try:
                            btn.click()
                            time.sleep(0.5)
                            logger.info(f"Closed right panel via: {selector}")
                        except Exception:
                            continue
                except Exception:
                    continue

            try:
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.send_keys(Keys.ESCAPE)
                time.sleep(0.3)
            except Exception:
                pass

            try:
                close_buttons = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    ".widgetbar-widget-close_button, "
                    "[class*='closeButton'], "
                    "[class*='right-widget'] [class*='close'], "
                    ".layout__area--right button[class*='close']"
                )
                for btn in close_buttons:
                    try:
                        btn.click()
                        time.sleep(0.3)
                        logger.info("Closed right panel via close button")
                    except Exception:
                        continue
            except Exception:
                pass

            time.sleep(1)

        except Exception as e:
            logger.warning(f"Could not close right panels: {e}")

    def _wait_for_indicator_panel(self) -> bool:
        """Wait for the Analysis indicator panel to render on the chart."""
        try:
            time.sleep(3)
            page_source = self.driver.page_source.lower()
            if "error" in page_source and "not found" in page_source:
                return False
            return True
        except Exception:
            return False

    def dismiss_popups(self):
        """Dismiss any TradingView popups, notifications, or cookie banners."""
        popup_selectors = [
            "[class*='cookie'] button",
            "[id*='cookie'] button",
            "[data-name='close']",
            ".tv-dialog__close",
            ".toast-close-button",
            "[class*='upgrade'] [class*='close']",
        ]
        for selector in popup_selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    try:
                        el.click()
                        time.sleep(0.5)
                    except Exception:
                        continue
            except Exception:
                continue

    def get_page_screenshot(self) -> bytes:
        """Take a full-page screenshot."""
        return self.driver.get_screenshot_as_png()

    def get_analysis_panel_screenshot(self) -> bytes:
        """
        Take a full-page screenshot for OCR.
        The chart has been scrolled right so candles are on the left
        and the Analysis panel has a clean background.
        """
        return self.driver.get_screenshot_as_png()
