"""
Browser automation: Selenium setup and TradingView authentication.
"""

import os
import json
import time
import logging
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)

COOKIES_PATH = Path(__file__).parent.parent.parent / "data" / "tv_cookies.json"


class TradingViewBrowser:
    """Manages Selenium browser instance with TradingView authentication."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self._setup_driver()

    def _setup_driver(self):
        """Initialize Chrome WebDriver with stable settings."""
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        # Core stability flags
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")

        # Extra stability
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-renderer-backgrounding")

        # Prevent detection
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            self.driver = webdriver.Chrome(options=options)

        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        self.driver.set_page_load_timeout(30)
        logger.info("Chrome WebDriver initialized")

    def login(self, username: str = None, password: str = None) -> bool:
        """
        Login to TradingView.
        Priority: 1) Cookie restoration  2) Credential login
        """
        username = username or os.getenv("TV_USERNAME")
        password = password or os.getenv("TV_PASSWORD")

        # Try cookie restoration first (most reliable for GitHub Actions)
        if self._restore_cookies():
            logger.info("Cookies restored - checking session...")
            # Try navigating to a chart page to verify cookies work
            if self._verify_session_with_chart():
                logger.info("Already logged in via persistent session")
                return True
            else:
                logger.warning("Cookie session expired or invalid, trying credentials...")

        # Credential-based login as fallback
        if not username or not password:
            logger.error("No credentials provided and cookie session failed")
            return False

        return self._login_with_credentials(username, password)

    def _verify_session_with_chart(self) -> bool:
        """
        Verify login by loading a chart page and checking for chart elements.
        More reliable than checking the homepage user menu on headless servers.
        """
        try:
            self.driver.get("https://www.tradingview.com/chart/")
            time.sleep(5)

            # If we get redirected to signin, cookies didn't work
            current_url = self.driver.current_url
            if "signin" in current_url or "sign-in" in current_url:
                logger.warning("Redirected to signin - cookies expired")
                return False

            # Check for chart elements (present only when logged in or on public charts)
            try:
                self.driver.find_element(By.CSS_SELECTOR, "canvas, .chart-container, .chart-markup-table")
                logger.info("Chart loaded successfully - session valid")
                return True
            except NoSuchElementException:
                pass

            # Check page source for indicators of logged-in state
            page_source = self.driver.page_source
            if "chart" in page_source.lower() and "signin" not in current_url:
                logger.info("Session appears valid (chart page loaded)")
                return True

            return False
        except Exception as e:
            logger.warning(f"Session verification error: {e}")
            return False

    def _is_logged_in(self) -> bool:
        """Check if currently logged in to TradingView."""
        try:
            self.driver.get("https://www.tradingview.com/")
            time.sleep(3)

            try:
                self.driver.find_element(By.CSS_SELECTOR, "[data-name='header-user-menu-button']")
                return True
            except NoSuchElementException:
                pass

            try:
                self.driver.find_element(By.CSS_SELECTOR, ".tv-header__user-menu-button")
                return True
            except NoSuchElementException:
                pass

            return False
        except Exception as e:
            logger.warning(f"Error checking login status: {e}")
            return False

    def _login_with_credentials(self, username: str, password: str) -> bool:
        """Perform credential-based login to TradingView."""
        try:
            logger.info("Attempting credential-based login...")
            self.driver.get("https://www.tradingview.com/#signin")
            time.sleep(5)

            # Click "Email" sign-in option
            wait = WebDriverWait(self.driver, 15)

            email_buttons = self.driver.find_elements(
                By.XPATH,
                "//*[contains(text(), 'Email') or contains(text(), 'email')]"
            )
            for btn in email_buttons:
                try:
                    btn.click()
                    time.sleep(2)
                    break
                except Exception:
                    continue

            # Enter username
            username_field = wait.until(
                EC.presence_of_element_located((By.NAME, "id_username"))
            )
            username_field.clear()
            username_field.send_keys(username)

            # Enter password
            password_field = self.driver.find_element(By.NAME, "id_password")
            password_field.clear()
            password_field.send_keys(password)

            # Click sign in - try multiple selectors
            sign_in_selectors = [
                "button[type='submit']",
                "[class*='submitButton']",
                "button[data-overflow-tooltip-text='Sign in']",
            ]
            clicked = False
            for selector in sign_in_selectors:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    btn.click()
                    clicked = True
                    break
                except Exception:
                    continue

            if not clicked:
                # Try XPath as last resort
                try:
                    btn = self.driver.find_element(
                        By.XPATH, "//button[contains(text(), 'Sign in') or contains(text(), 'Accedi')]"
                    )
                    btn.click()
                    clicked = True
                except Exception:
                    pass

            if not clicked:
                logger.error("Could not find sign-in button")
                return False

            time.sleep(5)

            # Handle 2FA if needed
            if self._handle_2fa():
                logger.info("2FA handled")

            # Verify login
            if self._verify_session_with_chart():
                self._save_cookies()
                logger.info("Login successful")
                return True
            else:
                logger.error("Login failed - could not verify session")
                return False

        except TimeoutException:
            logger.error("Login timed out")
            return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def _handle_2fa(self) -> bool:
        """Handle 2FA if prompted."""
        try:
            tfa_input = self.driver.find_elements(
                By.CSS_SELECTOR, "input[name='code'], input[placeholder*='code']"
            )
            if tfa_input:
                logger.warning(
                    "2FA detected! Run with headless=False for first login."
                )
                if not self.headless:
                    input("Press Enter after completing 2FA in the browser...")
                    return True
                return False
            return False
        except Exception:
            return False

    def _save_cookies(self):
        """Save browser cookies for session persistence."""
        try:
            COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            cookies = self.driver.get_cookies()
            with open(COOKIES_PATH, "w") as f:
                json.dump(cookies, f)
            logger.info(f"Cookies saved to {COOKIES_PATH}")
        except Exception as e:
            logger.warning(f"Failed to save cookies: {e}")

    def _restore_cookies(self) -> bool:
        """Restore cookies from file."""
        try:
            if not COOKIES_PATH.exists():
                logger.warning(f"Cookie file not found: {COOKIES_PATH}")
                return False

            logger.info(f"Loading cookies from {COOKIES_PATH}")

            # First navigate to the domain so we can set cookies
            self.driver.get("https://www.tradingview.com/")
            time.sleep(3)

            with open(COOKIES_PATH, "r") as f:
                cookies = json.load(f)

            logger.info(f"Found {len(cookies)} cookies to restore")

            restored = 0
            for cookie in cookies:
                try:
                    # Clean up cookie for Selenium
                    cookie.pop("sameSite", None)
                    cookie.pop("expiry", None)
                    self.driver.add_cookie(cookie)
                    restored += 1
                except Exception as e:
                    logger.debug(f"Skipped cookie {cookie.get('name', '?')}: {e}")
                    continue

            logger.info(f"Restored {restored}/{len(cookies)} cookies")

            if restored == 0:
                return False

            # Refresh to apply cookies
            self.driver.refresh()
            time.sleep(3)
            return True

        except Exception as e:
            logger.warning(f"Failed to restore cookies: {e}")
            return False

    def get_driver(self):
        """Return the Selenium WebDriver instance."""
        return self.driver

    def close(self):
        """Close the browser."""
        if self.driver:
            try:
                self._save_cookies()
            except Exception:
                pass
            try:
                self.driver.quit()
            except Exception:
                pass
            logger.info("Browser closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
