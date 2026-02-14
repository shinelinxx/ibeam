"""Load YAML config and inject values into os.environ as IBEAM_* variables.

The YAML structure uses nested keys that map to flat IBEAM_ environment variables.
For example:

    ibgw_account:
      username: myuser
      password: mypass
    twoFa:
      handler: TOTP
      totpSecret: XXXX

Maps to:
    IBEAM_ACCOUNT=myuser
    IBEAM_PASSWORD=mypass
    IBEAM_TWO_FA_HANDLER=TOTP
    IBEAM_TOTP_SECRET=XXXX
"""

import logging
import os
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)

# Mapping from YAML dot-paths to IBEAM_ env var names.
# Only keys listed here are recognized.
_YAML_TO_ENV = {
    # ibgw_account
    'ibgw_account.username': 'IBEAM_ACCOUNT',
    'ibgw_account.password': 'IBEAM_PASSWORD',
    'ibgw_account.key': 'IBEAM_KEY',

    # twoFa
    'twoFa.handler': 'IBEAM_TWO_FA_HANDLER',
    'twoFa.totpSecret': 'IBEAM_TOTP_SECRET',
    'twoFa.totpDigits': 'IBEAM_TOTP_DIGITS',
    'twoFa.totpPeriod': 'IBEAM_TOTP_PERIOD',
    'twoFa.selectTarget': 'IBEAM_TWO_FA_SELECT_TARGET',
    'twoFa.strictCode': 'IBEAM_STRICT_TWO_FA_CODE',
    'twoFa.customHandler': 'IBEAM_CUSTOM_TWO_FA_HANDLER',

    # gateway
    'gateway.baseUrl': 'IBEAM_GATEWAY_BASE_URL',
    'gateway.startup': 'IBEAM_GATEWAY_STARTUP',
    'gateway.processMatch': 'IBEAM_GATEWAY_PROCESS_MATCH',
    'gateway.dir': 'IBEAM_GATEWAY_DIR',

    # routes
    'gateway.routes.auth': 'IBEAM_ROUTE_AUTH',
    'gateway.routes.validate': 'IBEAM_ROUTE_VALIDATE',
    'gateway.routes.reauthenticate': 'IBEAM_ROUTE_REAUTHENTICATE',
    'gateway.routes.initialise': 'IBEAM_ROUTE_INITIALISE',
    'gateway.routes.authStatus': 'IBEAM_ROUTE_AUTH_STATUS',
    'gateway.routes.tickle': 'IBEAM_ROUTE_TICKLE',
    'gateway.routes.logout': 'IBEAM_ROUTE_LOGOUT',

    # auth (browser automation)
    'auth.oauthTimeout': 'IBEAM_OAUTH_TIMEOUT',
    'auth.pageLoadTimeout': 'IBEAM_PAGE_LOAD_TIMEOUT',
    'auth.errorScreenshots': 'IBEAM_ERROR_SCREENSHOTS',
    'auth.maxFailedAuth': 'IBEAM_MAX_FAILED_AUTH',
    'auth.maxImmediateAttempts': 'IBEAM_MAX_IMMEDIATE_ATTEMPTS',
    'auth.minPresubmitBuffer': 'IBEAM_MIN_PRESUBMIT_BUFFER',
    'auth.maxPresubmitBuffer': 'IBEAM_MAX_PRESUBMIT_BUFFER',
    'auth.strategy': 'IBEAM_AUTHENTICATION_STRATEGY',
    'auth.usePaperAccount': 'IBEAM_USE_PAPER_ACCOUNT',
    'auth.uiScaling': 'IBEAM_UI_SCALING',

    # service
    'service.maintenanceInterval': 'IBEAM_MAINTENANCE_INTERVAL',
    'service.requestRetries': 'IBEAM_REQUEST_RETRIES',
    'service.requestTimeout': 'IBEAM_REQUEST_TIMEOUT',
    'service.restartFailedSessions': 'IBEAM_RESTART_FAILED_SESSIONS',
    'service.restartWait': 'IBEAM_RESTART_WAIT',
    'service.reauthenticateWait': 'IBEAM_REAUTHENTICATE_WAIT',
    'service.maxStatusCheckRetries': 'IBEAM_MAX_STATUS_CHECK_RETRIES',
    'service.maxReauthenticateRetries': 'IBEAM_MAX_REAUTHENTICATE_RETRIES',
    'service.healthServerPort': 'IBEAM_HEALTH_SERVER_PORT',
    'service.spawnNewProcesses': 'IBEAM_SPAWN_NEW_PROCESSES',
    'service.startActive': 'IBEAM_START_ACTIVE',

    # logging
    'logging.level': 'IBEAM_LOG_LEVEL',
    'logging.toFile': 'IBEAM_LOG_TO_FILE',
    'logging.format': 'IBEAM_LOG_FORMAT',

    # directories
    'dirs.inputs': 'IBEAM_INPUTS_DIR',
    'dirs.outputs': 'IBEAM_OUTPUTS_DIR',

    # secrets
    'secrets.source': 'IBEAM_SECRETS_SOURCE',
    'secrets.gcpUrl': 'IBEAM_GCP_SECRETS_URL',

    # element selectors (advanced)
    'elements.userName': 'IBEAM_USER_NAME_EL',
    'elements.password': 'IBEAM_PASSWORD_EL',
    'elements.submit': 'IBEAM_SUBMIT_EL',
    'elements.error': 'IBEAM_ERROR_EL',
    'elements.successText': 'IBEAM_SUCCESS_EL_TEXT',
    'elements.livePaperToggle': 'IBEAM_LIVE_PAPER_TOGGLE_EL',
    'elements.twoFaEl': 'IBEAM_TWO_FA_EL_ID',
    'elements.twoFaNotification': 'IBEAM_TWO_FA_NOTIFICATION_EL',
    'elements.twoFaInput': 'IBEAM_TWO_FA_INPUT_EL_ID',
    'elements.twoFaSelect': 'IBEAM_TWO_FA_SELECT_EL_ID',
    'elements.ibkeyPromo': 'IBEAM_IBKEY_PROMO_EL_CLASS',
}


def _flatten(d: dict, parent_key: str = '') -> dict:
    """Flatten a nested dict with dot-separated keys."""
    items = {}
    for k, v in d.items():
        new_key = f'{parent_key}.{k}' if parent_key else k
        if isinstance(v, dict):
            items.update(_flatten(v, new_key))
        else:
            items[new_key] = v
    return items


def load_yaml_config(config_path: Optional[str] = None) -> int:
    """Load a YAML config file and set corresponding IBEAM_* env vars.

    Environment variables already set take precedence (not overwritten).

    Args:
        config_path: Path to the YAML file. If None, tries default locations.

    Returns:
        Number of variables set.
    """
    try:
        import yaml
    except ImportError:
        _LOGGER.debug('PyYAML not installed, skipping YAML config loading')
        return 0

    if config_path is None:
        # Support CONFIG_FILE env var (like ibkr-trader)
        config_path = os.environ.get('CONFIG_FILE')

    if config_path is None:
        # Try default locations:
        #   1. config.yaml in CWD (docker volume mount to WORKDIR)
        #   2. /srv/config/service-{TRADER_INDEX}.yaml (legacy)
        #   3. local dev: config/service-{TRADER_INDEX}.yaml relative to project root
        trader_index = os.environ.get('TRADER_INDEX', '0')
        project_root = os.path.join(os.path.dirname(__file__), '..', '..')
        candidates = [
            os.path.join(os.getcwd(), 'config.yaml'),
            f'/srv/config/service-{trader_index}.yaml',
            os.path.join(project_root, f'config/service-{trader_index}.yaml'),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                config_path = candidate
                break

    if config_path is None or not os.path.isfile(config_path):
        _LOGGER.debug(f'No YAML config file found')
        return 0

    _LOGGER.info(f'Loading config from {config_path}')

    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        _LOGGER.warning(f'Config file {config_path} is empty or invalid')
        return 0

    flat = _flatten(data)
    count = 0

    for yaml_key, env_key in _YAML_TO_ENV.items():
        if yaml_key in flat and flat[yaml_key] is not None:
            # Existing env vars take precedence
            if env_key not in os.environ:
                os.environ[env_key] = str(flat[yaml_key])
                count += 1

    _LOGGER.info(f'Loaded {count} config values from YAML')
    return count
