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

    def __init__(self, headless: bool = True, download_dir: str = None):
        self.headless = headless
        self.driver = None
        self._download_dir = download_dir
        self._setup_driver()

    def _setup_driver(self):
        """Initialize Chrome WebDriver with optimized settings."""
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")

        # Prevent detection
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Set download directory for CSV extraction
        if self._download_dir:
            os.makedirs(self._download_dir, exist_ok=True)
            prefs = {
                "download.default_directory": self._download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            }
            options.add_experimental_option("prefs", prefs)
            logger.info(f"Chrome download directory set to: {self._download_dir}")

        # Persistent user data dir to maintain sessions
        user_data_dir = Path(__file__).parent.parent.parent / "data" / "chrome_profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={user_data_dir}")

        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            # Fallback: try system chromedriver
            self.driver = webdriver.Chrome(options=options)

        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        self.driver.set_page_load_timeout(90)
        logger.info("Chrome WebDriver initialized")

    def login(self, username: str = None, password: str = None) -> bool:
        """
        Login to TradingView.
        First attempts session restoration from cookies/profile.
        Falls back to credential-based login.
        """
        username = username or os.getenv("TV_USERNAME")
        password = password or os.getenv("TV_PASSWORD")

        # Check if already logged in (persistent profile)
        if self._is_logged_in():
            logger.info("Already logged in via persistent session")
            return True

        # Try cookie restoration
        if self._restore_cookies():
            if self._is_logged_in():
                logger.info("Logged in via restored cookies")
                return True

        # Credential-based login
        if not username or not password:
            logger.error("No credentials provided and no active session")
            return False

        return self._login_with_credentials(username, password)

    def _is_logged_in(self) -> bool:
        """Check if currently logged in to TradingView."""
        try:
            self.driver.get("https://www.tradingview.com/")
            time.sleep(3)

            # Check for user menu (indicates logged in)
            try:
                self.driver.find_element(By.CSS_SELECTOR, "[data-name='header-user-menu-button']")
                return True
            except NoSuchElementException:
                pass

            # Alternative check
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
            time.sleep(3)

            # Click "Email" sign-in option
            wait = WebDriverWait(self.driver, 10)

            # Look for email sign-in button
            email_buttons = self.driver.find_elements(
                By.XPATH,
                "//*[contains(text(), 'Email') or contains(text(), 'email')]"
            )
            for btn in email_buttons:
                try:
                    btn.click()
                    time.sleep(1)
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

            # Click sign in
            sign_in_btn = self.driver.find_element(
                By.CSS_SELECTOR, "button[type='submit']"
            )
            sign_in_btn.click()

            time.sleep(5)

            # Check for 2FA prompt
            if self._handle_2fa():
                logger.info("2FA handled")

            # Verify login
            if self._is_logged_in():
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
        """
        Handle 2FA if prompted.
        Returns True if 2FA was detected (user must handle manually in non-headless mode).
        """
        try:
            # Check if 2FA input is present
            tfa_input = self.driver.find_elements(
                By.CSS_SELECTOR, "input[name='code'], input[placeholder*='code']"
            )
            if tfa_input:
                logger.warning(
                    "2FA detected! If running headless, you'll need to handle this manually. "
                    "Consider running with headless=False for first login."
                )
                # In non-headless mode, wait for user to enter code
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
                return False

            self.driver.get("https://www.tradingview.com/")
            time.sleep(2)

            with open(COOKIES_PATH, "r") as f:
                cookies = json.load(f)

            for cookie in cookies:
                try:
                    # Remove problematic fields
                    cookie.pop("sameSite", None)
                    cookie.pop("expiry", None)
                    self.driver.add_cookie(cookie)
                except Exception:
                    continue

            self.driver.refresh()
            time.sleep(3)
            logger.info("Cookies restored")
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
            self.driver.quit()
            logger.info("Browser closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
