import logging
import time
from functools import partial
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ibeam.src.handlers.secrets_handler import SecretsHandler
from ibeam.src.login.driver import DriverFactory, BrowserSession, start_up_browser, save_screenshot, shut_down_browser
from ibeam.src.login.targets import (
    Targets, targets_from_versions, Target, identify_target,
    find_element, wait_for_any, wait_for_target,
)
from ibeam.src.two_fa_handlers.notification_resend_handler import NotificationResendTwoFaHandler
from ibeam.src.two_fa_handlers.two_fa_handler import TwoFaHandler
from ibeam.src.utils.py_utils import exception_to_string

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)


class AttemptException(Exception):
    def __init__(self, *args, cause: str, **kwargs):
        self.cause = cause
        super().__init__(*args, **kwargs)


def check_version(driver: BrowserSession) -> int:
    """Check for the IBKR website version.

    Versions:
    * 1 = available until March 2023
    * 2 = available from March 2023
    """
    page = driver.page
    try:
        loc = page.locator('[name="user_name"]')
        loc.wait_for(state='attached', timeout=5000)
        return 1
    except PlaywrightTimeoutError:
        pass

    try:
        loc = page.locator('[name="username"]')
        loc.wait_for(state='attached', timeout=5000)
        return 2
    except PlaywrightTimeoutError:
        pass

    _LOGGER.warning('Cannot determine the version of IBKR website, assuming version 1')
    return 1


def _wait_and_identify_trigger(targets: Targets,
                                driver: BrowserSession,
                                timeout: int,
                                *conditions,
                                skip_identify: bool = False,
                                ):
    """Wait for any of the given conditions and identify the target.

    Each condition is a tuple of (target, condition_type).
    Returns (element_info/locator, target).
    """
    info, target, locator = wait_for_any(driver.page, list(conditions), timeout)

    if skip_identify:
        return locator, None

    return info, target


def handle_two_fa(two_fa_handler: TwoFaHandler, driver: BrowserSession, strict_two_fa_code: bool) -> Optional[str]:
    _LOGGER.info(f'Attempting to acquire 2FA code from: {two_fa_handler}')

    try:
        two_fa_code = two_fa_handler.get_two_fa_code(driver)
        if two_fa_code is not None:
            two_fa_code = str(two_fa_code)
    except Exception as e:
        _LOGGER.error(f'Error encountered while acquiring 2FA code. \nException:\n{exception_to_string(e)}')
        return None

    _LOGGER.debug(f'2FA code returned: {two_fa_code}')

    if strict_two_fa_code and two_fa_code is not None and \
            (not two_fa_code.isdigit() or len(two_fa_code) != 6):
        _LOGGER.error(
            f'Illegal 2FA code returned: {two_fa_code}. '
            f'Ensure the 2FA code contains 6 digits or disable this check by setting IBEAM_STRICT_TWO_FA_CODE to False.')
        return None

    return two_fa_code


# Helper functions to build condition tuples
def _visible(target: Target):
    return (target, 'visible')

def _clickable(target: Target):
    return (target, 'clickable')

def _has_text(target: Target):
    return (target, 'has_text')

def _present(target: Target):
    return (target, 'present')


class LoginHandler():
    _VERSIONS = {
        1: {
            'USER_NAME_EL': 'NAME@@user_name',
            'ERROR_EL': 'CSS_SELECTOR@@.alert.alert-danger.margin-top-10'
        },
        2: {
            'USER_NAME_EL': 'NAME@@username',
            'ERROR_EL': 'CSS_SELECTOR@@.xyz-errormessage'
        }
    }

    def __init__(self,
                 secrets_handler: SecretsHandler,
                 two_fa_handler: TwoFaHandler,
                 driver_factory: DriverFactory,
                 targets: Targets,
                 base_url: str,
                 route_auth: str,
                 two_fa_select_target: str,
                 strict_two_fa_code: bool,
                 max_immediate_attempts: int,
                 oauth_timeout: int,
                 max_presubmit_buffer: int,
                 min_presubmit_buffer: int,
                 max_failed_auth: int,
                 outputs_dir: str,
                 use_paper_account: bool = False,
                 ):

        self.secrets_handler = secrets_handler
        self.two_fa_handler = two_fa_handler
        self.driver_factory = driver_factory
        self.targets = targets

        self.base_url = base_url
        self.route_auth = route_auth
        self.two_fa_select_target = two_fa_select_target
        self.strict_two_fa_code = strict_two_fa_code
        self.max_immediate_attempts = max_immediate_attempts
        self.oauth_timeout = oauth_timeout
        self.max_presubmit_buffer = max_presubmit_buffer
        self.min_presubmit_buffer = min_presubmit_buffer
        self.max_failed_auth = max_failed_auth
        self.outputs_dir = outputs_dir
        self.use_paper_account = use_paper_account

        self.failed_attempts = 0
        self.presubmit_buffer = self.min_presubmit_buffer

    def step_login(self,
                   targets: Targets,
                   wait_and_identify_trigger: callable,
                   driver: BrowserSession,
                   account: str,
                   password: str,
                   key: str,
                   presubmit_buffer: int,
                   ):

        page = driver.page

        user_name_loc = find_element(targets['USER_NAME'], driver)
        password_loc = find_element(targets['PASSWORD'], driver)

        wait_and_identify_trigger(_clickable(targets['USER_NAME']))

        if self.use_paper_account:
            _LOGGER.info('Switching to paper mode')
            live_paper_toggle_loc = find_element(targets['LIVE_PAPER_TOGGLE'], driver)
            live_paper_toggle_loc.click()
            time.sleep(3)

        user_name_loc.clear()
        password_loc.clear()

        user_name_loc.fill(account)

        if key is None:
            password_loc.fill(password)
        else:
            password_loc.fill(Fernet(key).decrypt(password.encode('utf-8')).decode("utf-8"))

        password_loc.press('Tab')

        time.sleep(presubmit_buffer)

        _LOGGER.info('Submitting the form')
        submit_loc = find_element(targets['SUBMIT'], driver)
        submit_loc.click()

        trigger, target = wait_and_identify_trigger(
            _has_text(targets['SUCCESS']),
            _visible(targets['TWO_FA']),
            _visible(targets['TWO_FA_SELECT']),
            _visible(targets['TWO_FA_NOTIFICATION']),
            _visible(targets['ERROR']),
            _clickable(targets['IBKEY_PROMO']),
        )

        return trigger, target

    def step_select_two_fa(self,
                           targets: Targets,
                           wait_and_identify_trigger: callable,
                           driver: BrowserSession,
                           two_fa_select_target: str,
                           ):
        _LOGGER.info(f'Required to select a 2FA method. Selecting: "{two_fa_select_target}"')
        select_loc = find_element(targets['TWO_FA_SELECT'], driver)
        select_loc.select_option(label=two_fa_select_target)

        trigger, target = wait_and_identify_trigger(
            _has_text(targets['SUCCESS']),
            _visible(targets['TWO_FA']),
            _visible(targets['TWO_FA_NOTIFICATION']),
            _visible(targets['ERROR']),
            _clickable(targets['IBKEY_PROMO'])
        )

        _LOGGER.info(f'2FA method "{two_fa_select_target}" selected successfully.')
        return trigger, target

    def step_two_fa_notification(self,
                                 targets: Targets,
                                 wait_and_identify_trigger: callable,
                                 driver: BrowserSession,
                                 two_fa_handler: TwoFaHandler,
                                 ):
        _LOGGER.info('Credentials correct, but Gateway requires notification two-factor authentication.')

        if two_fa_handler is not None:
            two_fa_handler_cast = two_fa_handler  # type: NotificationResendTwoFaHandler
            success_text = targets['SUCCESS'].identifier if isinstance(targets['SUCCESS'].identifier, str) else 'Client login succeeds'
            two_fa_success = two_fa_handler_cast.interact_with_notification(driver, success_text)
            if not two_fa_success:
                driver.refresh()
                raise AttemptException(cause='continue')

        trigger, target = wait_and_identify_trigger(
            _has_text(targets['SUCCESS']),
            _clickable(targets['IBKEY_PROMO']),
            _visible(targets['ERROR'])
        )
        return trigger, target

    def step_two_fa(self,
                    targets: Targets,
                    wait_and_identify_trigger: callable,
                    driver: BrowserSession,
                    two_fa_handler: TwoFaHandler,
                    strict_two_fa_code: bool,
                    ):
        _LOGGER.info('Credentials correct, but Gateway requires two-factor authentication.')
        if two_fa_handler is None:
            _LOGGER.critical(
                '######## ATTENTION! ######## No 2FA handler found. You may define your own 2FA handler or use built-in handlers. '
                'See documentation for more: https://github.com/Voyz/ibeam/wiki/Two-Factor-Authentication')
            raise AttemptException(cause='shutdown')

        two_fa_code = handle_two_fa(two_fa_handler, driver, strict_two_fa_code)

        if two_fa_code is None:
            _LOGGER.warning('No 2FA code returned. Aborting authentication.')
            raise AttemptException(cause='break')
        else:
            two_fa_input_loc = wait_for_target(
                driver.page, targets['TWO_FA_INPUT'], 'clickable', self.oauth_timeout
            )

            two_fa_input_loc.clear()
            two_fa_input_loc.fill(two_fa_code)

            _LOGGER.info('Submitting the 2FA form')
            two_fa_input_loc.press('Enter')

            trigger, target = wait_and_identify_trigger(
                _has_text(targets['SUCCESS']),
                _clickable(targets['IBKEY_PROMO']),
                _visible(targets['ERROR'])
            )

            return trigger, target

    def step_handle_ib_key_promo(self,
                                 driver: BrowserSession,
                                 targets: Targets,
                                 wait_and_identify_trigger: callable,
                                 ib_promo_key_trigger,
                                 ):
        _LOGGER.info('Handling IB-Key promo display...')
        time.sleep(3)

        # ib_promo_key_trigger could be a Locator or element_info dict
        if hasattr(ib_promo_key_trigger, 'click'):
            ib_promo_key_trigger.click(force=True)
        else:
            # Find the element again and force click
            loc = find_element(targets['IBKEY_PROMO'], driver)
            loc.click(force=True)

        trigger, target = wait_and_identify_trigger(
            _has_text(targets['SUCCESS']),
            _visible(targets['ERROR'])
        )

        return trigger, target

    def step_paper_toggle(self,
                          driver: BrowserSession,
                          targets: Targets,
                          wait_and_identify_trigger: callable
                          ):
        _LOGGER.info('Switching to paper mode and reattempting to submit the form')
        live_paper_toggle_loc = find_element(targets['LIVE_PAPER_TOGGLE'], driver)
        live_paper_toggle_loc.click()

        time.sleep(3)

        submit_loc = find_element(targets['SUBMIT'], driver)
        submit_loc.click()

        time.sleep(3)

        trigger, target = wait_and_identify_trigger(
            _has_text(targets['SUCCESS']),
            _visible(targets['TWO_FA']),
            _visible(targets['TWO_FA_SELECT']),
            _visible(targets['TWO_FA_NOTIFICATION']),
            _visible(targets['ERROR']),
            _clickable(targets['IBKEY_PROMO']),
        )

        return trigger, target

    def step_error(self,
                   driver: BrowserSession,
                   error_trigger,
                   max_presubmit_buffer: int,
                   max_failed_auth: int,
                   outputs_dir: str
                   ):
        # error_trigger is an element_info dict
        error_text = error_trigger.get('text', '') if isinstance(error_trigger, dict) else str(error_trigger)

        _LOGGER.error(f'Error displayed by the login webpage: {error_text}')
        save_screenshot(driver, outputs_dir, '__failed_attempt')
        if error_text == 'Invalid username password combination' and self.presubmit_buffer < max_presubmit_buffer:
            self.presubmit_buffer += 5
            if self.presubmit_buffer >= self.max_presubmit_buffer:
                self.presubmit_buffer = self.max_presubmit_buffer
                _LOGGER.warning(f'The presubmit buffer set to maximum: {self.max_presubmit_buffer}')
            else:
                _LOGGER.warning(f'Increased presubmit buffer to {self.presubmit_buffer}')

        if (error_text == 'failed' or error_text == 'Invalid username password combination') and max_failed_auth > 0:
            self.failed_attempts += 1
            if self.failed_attempts >= self.max_failed_auth:
                _LOGGER.critical(
                    f'######## ATTENTION! ######## Maximum number of failed authentication attempts '
                    f'(IBEAM_MAX_FAILED_AUTH={self.max_failed_auth}) reached. IBeam will shut down to prevent an account lock-out. '
                    f'It is recommended you attempt to authenticate manually in order to reset the counter. '
                    f'Read the execution logs and report issues at https://github.com/Voyz/ibeam/issues')
                raise AttemptException(cause='shutdown')

        time.sleep(1)
        raise AttemptException(cause='continue')

    def handle_timeout_exception(self,
                                 e: Exception,
                                 targets: Targets,
                                 driver: BrowserSession,
                                 website_version: int,
                                 route_auth: str,
                                 base_url: str,
                                 outputs_dir: str):
        page_loaded_correctly = True
        try:
            loc = driver.page.locator('.login')
            loc.wait_for(state='attached', timeout=5000)
        except Exception:
            page_loaded_correctly = False

        if not page_loaded_correctly or website_version == -1:
            _LOGGER.error(
                f'Timeout reached when waiting for authentication. The website seems to not be loaded correctly. '
                f'Consider increasing IBEAM_PAGE_LOAD_TIMEOUT. \nWebsite URL: {base_url + route_auth} '
                f'\n \nException:\n{exception_to_string(e)}')
        else:
            _LOGGER.error(
                f'Timeout reached searching for website elements, but the website seems to be loaded correctly. '
                f'It is possible the setup is incorrect. \nWebsite version: {website_version} '
                f'\nDOM elements searched for: {targets}. \nException:\n{exception_to_string(e)}')

        save_screenshot(driver, outputs_dir, '__timeout-exception')

    def step_failed_two_fa(self, driver: BrowserSession):
        time.sleep(1)
        driver.refresh()
        raise AttemptException(cause='continue')

    def step_success(self):
        _LOGGER.info('Webpage displayed "Client login succeeds"')
        self.failed_attempts = 0
        self.presubmit_buffer = self.min_presubmit_buffer
        raise AttemptException(cause='success')

    def attempt(
            self,
            targets: Targets,
            wait_and_identify_trigger: callable,
            driver: BrowserSession
    ):
        trigger, target = self.step_login(
            targets, wait_and_identify_trigger, driver,
            self.secrets_handler.account, self.secrets_handler.password,
            self.secrets_handler.key, self.presubmit_buffer
        )

        # Extract error text for paper mode check
        error_text = ''
        if isinstance(trigger, dict):
            error_text = trigger.get('text', '')

        if target == targets['ERROR'] and error_text == \
                'You have selected the Live Account Mode, but the specified user is a Paper Trading user. Please select the correct Login mode.':
            trigger, target = self.step_paper_toggle(driver, targets, wait_and_identify_trigger)

        if target == targets['TWO_FA_SELECT']:
            trigger, target = self.step_select_two_fa(targets, wait_and_identify_trigger, driver, self.two_fa_select_target)

        if target == targets['TWO_FA_NOTIFICATION']:
            trigger, target = self.step_two_fa_notification(targets, wait_and_identify_trigger, driver, self.two_fa_handler)

        if target == targets['TWO_FA']:
            trigger, target = self.step_two_fa(targets, wait_and_identify_trigger, driver, self.two_fa_handler, self.strict_two_fa_code)

        if target == targets['IBKEY_PROMO']:
            trigger, target = self.step_handle_ib_key_promo(driver, targets, wait_and_identify_trigger, trigger)

        if target == targets['ERROR']:
            self.step_error(driver, trigger, self.max_presubmit_buffer, self.max_failed_auth, self.outputs_dir)

        elif target == targets['TWO_FA']:
            self.step_failed_two_fa(driver)

        elif target == targets['SUCCESS']:
            self.step_success()

    def load_page(self, targets: Targets, driver: BrowserSession, base_url: str, route_auth: str):
        driver.get(base_url + route_auth)

        website_version = check_version(driver)

        targets = targets_from_versions(targets, self._VERSIONS[website_version])
        _LOGGER.debug(f'Targets: {targets}')

        wait_and_identify_trigger = partial(_wait_and_identify_trigger, targets, driver, self.oauth_timeout)

        wait_and_identify_trigger(_clickable(targets['USER_NAME']), skip_identify=True)
        _LOGGER.info('Gateway auth webpage loaded')

        return wait_and_identify_trigger

    def login(self) -> (bool, bool):
        """Logs into the currently running gateway.

        First boolean - whether authentication was successful
        Second boolean - whether max failed attempts was reached and IBeam should shut down

        :return: Whether authentication was successful and whether IBeam should shut down
        :rtype: (bool, bool)
        """
        display = None
        success = False
        driver = None
        website_version = -1
        targets = self.targets

        # No credentials configured — skip browser automation, let user log in manually
        if self.secrets_handler.account is None or self.secrets_handler.password is None:
            _LOGGER.warning('No credentials configured (ibgw_account not set). '
                            'Skipping automated login. Please open the Gateway login page '
                            f'in your browser and log in manually: {self.base_url + self.route_auth}')
            return False, False

        try:
            _LOGGER.info(f'Loading auth webpage at {self.base_url + self.route_auth}')
            driver, display = start_up_browser(self.driver_factory)

            wait_and_identify_trigger = self.load_page(targets, driver, self.base_url, self.route_auth)

            immediate_attempts = 0

            while immediate_attempts < max(self.max_immediate_attempts, 1):
                immediate_attempts += 1
                _LOGGER.info(f'Login attempt number {immediate_attempts}')

                try:
                    wait_and_identify_trigger(_clickable(targets['USER_NAME']), skip_identify=True)
                except (PlaywrightTimeoutError, Exception) as e:
                    _LOGGER.info(f'Page loaded but {targets["USER_NAME"]} is not clickable. Reloading webpage.')
                    wait_and_identify_trigger = self.load_page(targets, driver, self.base_url, self.route_auth)

                try:
                    self.attempt(targets, wait_and_identify_trigger, driver)
                except AttemptException as e:
                    if e.cause == 'continue':
                        continue
                    elif e.cause == 'success':
                        success = True
                        break
                    elif e.cause == 'shutdown':
                        return False, True
                    elif e.cause == 'break':
                        break
                    else:
                        raise RuntimeError(f'Invalid AttemptException: {e}')

            time.sleep(1)
        except PlaywrightTimeoutError as e:
            self.handle_timeout_exception(e, targets, driver, website_version, self.route_auth, self.base_url, self.outputs_dir)
            success = False
        except Exception as e:
            _LOGGER.error(f'Error encountered during authentication \nException:\n{exception_to_string(e)}')
            save_screenshot(driver, self.outputs_dir, '__generic-exception')
            success = False
        finally:
            shut_down_browser(driver, display)

        return success, False
