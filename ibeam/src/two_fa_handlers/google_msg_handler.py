import logging
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ibeam.src.login.driver import release_chrome_driver, save_screenshot, DriverFactory, BrowserSession
from ibeam.src.two_fa_handlers.two_fa_handler import TwoFaHandler

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)

_GOOG_QR_CODE_CLASS = os.environ.get('IBEAM_GOOG_QR_CODE_CLASS', 'bigger-qr-code')
"""HTML element indicating web messages needs authorization."""

_GOOG_QR_CODE_DATA = os.environ.get('IBEAM_GOOG_QR_CODE_DATA', 'qr-code')
"""HTML data attribute with the qr code."""

_GOOG_AUTH_REMEMBER_CLASS = os.environ.get('IBEAM_GOOG_AUTH_REMEMBER_CLASS', 'local-storage-checkbox')
"""HTML element to remember web messages device pairing."""

_GOOG_MESSAGES_LIST_CLASS = os.environ.get('IBEAM_GOOG_MESSAGES_LIST_CLASS', '.text-content.unread .snippet-text')
"""HTML element indicating web messages has loaded."""

_GOOG_2FA_HEADING = os.environ.get('IBEAM_GOOG_2FA_HEADING', 'Your requested authentication code')
"""HTML element text indicating 2fa message received."""

_GOOG_MESSAGE_CLICK_RETRIES = int(os.environ.get('IBEAM_GOOG_MESSAGE_CLICK_RETRIES', 5))
"""How many times to try marking the message as read."""


class GoogleMessagesTwoFaHandler(TwoFaHandler):

    def __init__(self, driver_factory: DriverFactory, *args, **kwargs):
        self.driver_factory = driver_factory
        super().__init__(*args, **kwargs)

    def get_two_fa_code(self, _) -> Optional[str]:
        code_two_fa = None

        driver_2fa: BrowserSession = self.driver_factory.new_driver(name='google_msg', incognito=False)
        if driver_2fa is None:
            return None

        try:
            page = driver_2fa.page
            page.goto('https://messages.google.com/web', wait_until='domcontentloaded')

            qr_loc = page.locator(f'.{_GOOG_QR_CODE_CLASS}')
            sms_loc = page.locator(_GOOG_MESSAGES_LIST_CLASS).filter(has_text=_GOOG_2FA_HEADING)

            # Wait for either QR code or SMS message
            deadline = time.time() + 240
            found_qr = False
            found_sms = False
            while time.time() < deadline:
                if qr_loc.count() > 0 and qr_loc.first.is_visible():
                    found_qr = True
                    break
                if sms_loc.count() > 0:
                    found_sms = True
                    break
                time.sleep(1)

            if not found_qr and not found_sms:
                _LOGGER.error('Timeout waiting for QR code or SMS message.')
                save_screenshot(driver_2fa, self.outputs_dir, postfix='__google_2fa')
                return None

            if found_qr:
                page.locator(f'.{_GOOG_AUTH_REMEMBER_CLASS}').click()

                data = urllib.parse.quote(
                    qr_loc.first.get_attribute(f'data-{_GOOG_QR_CODE_DATA}') or ''
                )

                _LOGGER.info(
                    'Web messages is not authenticated. Open this URL to pair web messages with your android phone:')
                _LOGGER.info(
                    f'http://api.qrserver.com/v1/create-qr-code/?color=000000&bgcolor=FFFFFF&qzone=1&margin=0&size=400x400&ecc=L&data={data}')

                try:
                    sms_loc.first.wait_for(state='visible', timeout=120000)
                except PlaywrightTimeoutError:
                    _LOGGER.error('Timeout waiting for SMS after QR pairing.')
                    save_screenshot(driver_2fa, self.outputs_dir, postfix='__google_2fa')
                    return None

            sms_elements = page.locator(_GOOG_MESSAGES_LIST_CLASS)

            if sms_elements.count() == 0:
                _LOGGER.error('Timeout or authentication error while loading sms messages.')
                save_screenshot(driver_2fa, self.outputs_dir, postfix='__google_2fa')
            else:
                first_sms = sms_elements.first
                sms_text = first_sms.inner_text()
                _LOGGER.info(f'First SMS found: "{sms_text}"')

                match = re.search(r'(\d+)', sms_text)
                if match:
                    code_two_fa = match.group(1)

                _LOGGER.info('Waiting for SMS message to be visible')
                first_sms.wait_for(state='visible', timeout=30000)

                clicked_ok = False
                for i in range(_GOOG_MESSAGE_CLICK_RETRIES):
                    try:
                        first_sms.click()
                        clicked_ok = True
                        _LOGGER.info('SMS message marked as read')
                        break
                    except Exception as e:
                        if 'intercept' in str(e).lower():
                            _LOGGER.warning('Failed marking SMS message as read due to obstructing elements')
                        else:
                            _LOGGER.exception(f'Exception while marking SMS message as read: {e}')

                        save_screenshot(driver_2fa, self.outputs_dir, postfix='__google_2fa')
                        _LOGGER.info(f'Retrying clicking SMS message {_GOOG_MESSAGE_CLICK_RETRIES - i - 1} more times.')
                        time.sleep(2)

                if not clicked_ok:
                    _LOGGER.warning('Failed all attempts to mark SMS message as read')

                time.sleep(2)

        except Exception:
            save_screenshot(driver_2fa, self.outputs_dir, '__google-msg')
            raise
        finally:
            _LOGGER.info(f'Cleaning up the resources. Google MSG Driver: {driver_2fa}')
            release_chrome_driver(driver_2fa)

        return code_two_fa

    def __str__(self):
        return f"GoogleMessagesTwoFaHandler(driver_path={self.driver_factory.driver_path})"
