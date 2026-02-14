import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright, Error as PlaywrightError

import ibeam
from ibeam.src import var

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)


class DriverFactory():
    def __init__(self,
                 driver_path: str = None,
                 name: str = 'default',
                 headless: bool = True,
                 incognito: bool = True,
                 ui_scaling: float = 1,
                 page_load_timeout: int = 15
                 ):
        self.driver_path = driver_path
        self.name = name
        self.headless = headless
        self.incognito = incognito
        self.ui_scaling = ui_scaling
        self.page_load_timeout = page_load_timeout

    def new_driver(self,
                   driver_path: str = None,
                   name: str = None,
                   headless: bool = None,
                   incognito: bool = None,
                   ui_scaling: float = None,
                   page_load_timeout: int = None
                   ) -> 'BrowserSession':

        headless = headless if headless is not None else self.headless
        incognito = incognito if incognito is not None else self.incognito
        ui_scaling = ui_scaling if ui_scaling is not None else self.ui_scaling
        page_load_timeout = page_load_timeout if page_load_timeout is not None else self.page_load_timeout
        name = name if name is not None else self.name

        return start_driver(name=name, headless=headless, incognito=incognito,
                            ui_scaling=ui_scaling, page_load_timeout=page_load_timeout)


class BrowserSession():
    """Wraps Playwright browser, context, and page into a single object
    that provides a similar interface to the old Selenium driver."""

    def __init__(self, playwright: Playwright, browser: Browser, context: BrowserContext, page: Page):
        self.playwright = playwright
        self.browser = browser
        self.context = context
        self.page = page

    def get(self, url: str):
        self.page.goto(url, wait_until='domcontentloaded')

    def quit(self):
        try:
            self.context.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass

    def refresh(self):
        self.page.reload(wait_until='domcontentloaded')

    def execute_script(self, script: str, *args):
        if args:
            return self.page.evaluate(script.replace('arguments[0]', 'el'), args[0])
        return self.page.evaluate(script)


def start_driver(name: str = 'default',
                 headless: bool = True,
                 incognito: bool = True,
                 ui_scaling: float = 1,
                 page_load_timeout: int = 15
                 ) -> Optional[BrowserSession]:
    try:
        # APScheduler 的线程可能已有 asyncio event loop，
        # Playwright sync API 检测到已有 loop 时会报错，
        # 暂时移除当前线程的 event loop，用完再恢复。
        old_loop = None
        try:
            old_loop = asyncio.get_event_loop()
            asyncio.set_event_loop(None)
        except RuntimeError:
            pass

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=headless,
                args=[
                    '--no-sandbox',
                    '--ignore-certificate-errors',
                    '--disable-extensions',
                    '--dns-prefetch-disable',
                    '--disable-features=VizDisplayCompositor',
                    f'--force-device-scale-factor={ui_scaling}',
                ]
            )

            context_args = {
                'ignore_https_errors': True,
                'viewport': {'width': 800, 'height': 600},
            }

            context = browser.new_context(**context_args)
            context.set_default_navigation_timeout(page_load_timeout * 1000)
            context.set_default_timeout(page_load_timeout * 1000)

            page = context.new_page()

            return BrowserSession(pw, browser, context, page)
        finally:
            if old_loop is not None:
                asyncio.set_event_loop(old_loop)

    except PlaywrightError as e:
        error_msg = str(e)
        if 'net::ERR_CONNECTION_REFUSED' in error_msg:
            _LOGGER.error(
                'Connection to Gateway refused. This could indicate IB Gateway is not running. '
                'Consider increasing IBEAM_GATEWAY_STARTUP wait buffer')
            return None
        if 'net::ERR_CONNECTION_CLOSED' in error_msg:
            _LOGGER.error(
                'Connection to Gateway failed. This could indicate IB Gateway is not running correctly '
                'or that its port was already occupied')
            return None
        else:
            raise


def release_chrome_driver(driver: BrowserSession):
    if driver is not None:
        driver.quit()


def save_screenshot(driver: Optional[BrowserSession], outputs_dir, postfix='', _depth=0):
    if not var.ERROR_SCREENSHOTS or driver is None:
        return

    if _depth > 10:
        _LOGGER.warning('Too many screenshot name collisions, skipping screenshot.')
        return

    now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    outputs_path = Path(outputs_dir)
    screenshot_name = f'ibeam__{ibeam.__version__}__{now}{postfix}.png'

    try:
        outputs_path.mkdir(exist_ok=True)
        screenshot_filepath = os.path.join(outputs_dir, screenshot_name)

        if os.path.exists(screenshot_filepath):
            save_screenshot(driver, outputs_dir, postfix + '_', _depth + 1)
            return

        _LOGGER.info(
            f'Saving screenshot to {screenshot_filepath}. '
            f'Make sure to cover your credentials if you share it with others.')
        driver.page.screenshot(path=screenshot_filepath, full_page=True)
    except Exception as e:
        _LOGGER.exception(f"Exception while saving screenshot: {str(e)} for screenshot: {screenshot_name}")


def start_up_browser(driver_factory: DriverFactory) -> (BrowserSession, None):
    """Start browser. Playwright handles its own display, no need for pyvirtualdisplay."""
    driver = driver_factory.new_driver()
    if driver is None:
        return None, None

    return driver, None


def shut_down_browser(driver: Optional[BrowserSession], display=None):
    _LOGGER.info(f'Cleaning up the resources. Driver: {driver}')

    if driver is not None:
        release_chrome_driver(driver)
