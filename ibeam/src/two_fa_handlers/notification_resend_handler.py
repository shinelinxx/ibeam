import logging
import os
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ibeam.src.login.driver import save_screenshot, BrowserSession
from ibeam.src.two_fa_handlers.two_fa_handler import TwoFaHandler

_NOTIFICATION_RESEND_RETRIES = int(os.environ.get('IBEAM_NOTIFICATION_RESEND_RETRIES', 10))
"""How many times to resend the notification."""

_NOTIFICATION_RESEND_INTERVAL = int(os.environ.get('IBEAM_NOTIFICATION_RESEND_INTERVAL', 10))
"""How many seconds between resend attempts."""

_NOTIFICATION_RESEND_EL = os.environ.get('IBEAM_NOTIFICATION_RESEND_EL', "a[onclick*='resendNotification()']")
"""CSS selector for the resend notification button."""

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)


class NotificationResendTwoFaHandler(TwoFaHandler):
    """This 2FA handler will repeatedly resend notifications to user's phone."""

    def check_and_resend(self, driver: BrowserSession, success_text: str, depth=0):
        if depth >= _NOTIFICATION_RESEND_RETRIES:
            _LOGGER.error(
                f'Reached maximum number of notification resend retries: {_NOTIFICATION_RESEND_RETRIES}. Aborting.')
            return False

        page = driver.page
        try:
            resend_loc = page.locator(_NOTIFICATION_RESEND_EL)
            resend_loc.wait_for(state='visible', timeout=30000)
            resend_loc.click()
        except PlaywrightTimeoutError:
            _LOGGER.error(f'Notification resend element not found: {_NOTIFICATION_RESEND_EL}. Aborting.')
            return False

        try:
            # Wait for success text to appear
            page.locator('pre, body').filter(has_text=success_text).wait_for(
                state='visible', timeout=_NOTIFICATION_RESEND_INTERVAL * 1000
            )
            return True
        except PlaywrightTimeoutError:
            _LOGGER.info(
                f'Success condition was not found when resending 2FA notification. '
                f'Reattempting {_NOTIFICATION_RESEND_RETRIES - depth - 1} more times.')
            return self.check_and_resend(driver, success_text, depth + 1)

    def get_two_fa_code(self, driver) -> Optional[bool]:
        raise NotImplementedError()

    def interact_with_notification(self, driver: BrowserSession, success_text: str) -> Optional[bool]:
        time.sleep(2)
        try:
            return self.check_and_resend(driver, success_text)
        except Exception as e:
            _LOGGER.exception(f'Exception while handling notification resend 2FA: {e}')
            save_screenshot(driver, self.outputs_dir, postfix='__notification_2fa')

    def __str__(self):
        return "NotificationResendTwoFaHandler()"
