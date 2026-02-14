import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, Locator, expect

from ibeam.config import Config

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)


class Target():
    def __init__(self, variable: str):
        type_str, identifier = variable.split("@@")
        self.type = type_str
        self.identifier = identifier
        self.variable = variable

    def locator(self, page: Page) -> Locator:
        """Return a Playwright Locator for this target."""
        if self.type == 'ID':
            return page.locator(f'#{self.identifier}')
        elif self.type == 'CSS_SELECTOR':
            return page.locator(self.identifier)
        elif self.type == 'CLASS_NAME':
            return page.locator(f'.{self.identifier}')
        elif self.type == 'NAME':
            return page.locator(f'[name="{self.identifier}"]')
        elif self.type == 'FOR':
            return page.locator(self.identifier)
        elif self.type == 'TAG_NAME':
            # TAG_NAME@@text means we look for the text in <pre> or <body>
            return page.locator('pre, body')
        else:
            raise RuntimeError(f'Unknown target type: {self.type}@@{self.identifier}')

    def identify(self, element_info: dict) -> bool:
        """Identify if a trigger matches this target based on element attributes."""
        if self.type == 'ID':
            return self.identifier in (element_info.get('id') or '')
        elif self.type == 'CSS_SELECTOR':
            return self.identifier.replace('.', ' ').strip() in (element_info.get('class') or '')
        elif self.type == 'CLASS_NAME':
            return self.identifier in (element_info.get('class') or '')
        elif self.type == 'NAME':
            return self.identifier in (element_info.get('name') or '')
        elif self.type == 'TAG_NAME':
            return self.identifier in (element_info.get('text') or '')
        elif self.type == 'FOR':
            return self.identifier in (element_info.get('for') or '')
        return False

    def __repr__(self):
        return f'Target({self.variable})'


Targets = dict[str, Target]


def targets_from_versions(targets: Targets, versions: dict) -> Targets:
    version_target_user_name = Target(versions['USER_NAME_EL'])
    version_target_error = Target(versions['ERROR_EL'])

    if 'USER_NAME' in targets and version_target_user_name.variable != targets['USER_NAME'].variable:
        _LOGGER.warning(
            f'USER_NAME target is forced to "{targets["USER_NAME"].variable}", '
            f'contrary to the element found on the website: "{version_target_user_name}"')
    else:
        targets['USER_NAME'] = version_target_user_name

    if "ERROR" in targets and version_target_error.variable != targets['ERROR'].variable:
        _LOGGER.warning(
            f'ERROR target is forced to "{targets["ERROR"].variable}", '
            f'contrary to the element found on the website: "{version_target_error}"')
    else:
        targets['ERROR'] = version_target_error

    return targets


def create_targets(cnf: Config) -> Targets:
    targets = {}

    targets['PASSWORD'] = Target(cnf.PASSWORD_EL)
    targets['SUBMIT'] = Target(cnf.SUBMIT_EL)
    targets['SUCCESS'] = Target(cnf.SUCCESS_EL_TEXT)
    targets['IBKEY_PROMO'] = Target(cnf.IBKEY_PROMO_EL_CLASS)
    targets['TWO_FA'] = Target(cnf.TWO_FA_EL_ID)
    targets['TWO_FA_NOTIFICATION'] = Target(cnf.TWO_FA_NOTIFICATION_EL)
    targets['TWO_FA_INPUT'] = Target(cnf.TWO_FA_INPUT_EL_ID)
    targets['TWO_FA_SELECT'] = Target(cnf.TWO_FA_SELECT_EL_ID)
    targets['LIVE_PAPER_TOGGLE'] = Target(cnf.LIVE_PAPER_TOGGLE_EL)

    return targets


def find_element(target: Target, driver) -> Locator:
    """Return a Playwright Locator for the target."""
    return target.locator(driver.page)


def identify_target(element_info: dict, targets: Targets) -> Optional[Target]:
    """Identify which target matches the element info dict."""
    for target in targets.values():
        try:
            if target.identify(element_info):
                return target
        except TypeError:
            continue

    raise RuntimeError(f'Trigger found but cannot be identified: {element_info}')


def wait_for_any(page: Page, targets_list: list, timeout: int) -> (dict, Target, Locator):
    """Wait for any of the given (target, condition) pairs to match.

    Each item in targets_list is a tuple of (target, condition_type) where condition_type
    is one of: 'visible', 'clickable', 'has_text', 'present'.

    Returns: (element_info dict, matched Target, Locator)
    """
    # Build a combined selector that matches any of our targets
    # We'll poll until one matches
    import time
    deadline = time.time() + timeout

    while time.time() < deadline:
        for target, condition in targets_list:
            try:
                loc = target.locator(page)
                if condition == 'has_text':
                    # For text targets (TAG_NAME@@text), check text content
                    for tag in ['pre', 'body']:
                        try:
                            tag_loc = page.locator(tag)
                            if tag_loc.count() > 0:
                                text = tag_loc.first.inner_text(timeout=500)
                                if target.identifier in text:
                                    info = {'text': text, 'tag': tag}
                                    return info, target, tag_loc.first
                        except Exception:
                            continue
                elif condition == 'visible':
                    if loc.count() > 0 and loc.first.is_visible():
                        info = _get_element_info(loc.first)
                        return info, target, loc.first
                elif condition == 'clickable':
                    if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                        info = _get_element_info(loc.first)
                        return info, target, loc.first
                elif condition == 'present':
                    if loc.count() > 0:
                        info = _get_element_info(loc.first)
                        return info, target, loc.first
            except Exception:
                continue
        time.sleep(0.3)

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    raise PlaywrightTimeoutError(f'Timeout waiting for any of {[t[0] for t in targets_list]}')


def wait_for_target(page: Page, target: Target, condition: str, timeout: int) -> Locator:
    """Wait for a single target with a condition. Returns the Locator."""
    info, matched, loc = wait_for_any(page, [(target, condition)], timeout)
    return loc


def _get_element_info(locator: Locator) -> dict:
    """Extract element attributes for identification."""
    try:
        return {
            'id': locator.get_attribute('id') or '',
            'class': locator.get_attribute('class') or '',
            'name': locator.get_attribute('name') or '',
            'for': locator.get_attribute('for') or '',
            'text': locator.inner_text(timeout=1000) if _is_text_element(locator) else '',
            'tag': locator.evaluate('el => el.tagName.toLowerCase()'),
        }
    except Exception:
        return {}


def _is_text_element(locator: Locator) -> bool:
    try:
        tag = locator.evaluate('el => el.tagName.toLowerCase()')
        return tag in ('pre', 'body', 'span', 'div', 'p', 'label')
    except Exception:
        return False
