import base64
import hashlib
import hmac
import logging
import os
import struct
import time
from pathlib import Path
from typing import Optional

from ibeam.src.two_fa_handlers.two_fa_handler import TwoFaHandler

_LOGGER = logging.getLogger('ibeam.' + Path(__file__).stem)

_TOTP_SECRET = os.environ.get('IBEAM_TOTP_SECRET', None)
"""Base32-encoded TOTP secret key from IBKR's Secure Login System."""

_TOTP_DIGITS = int(os.environ.get('IBEAM_TOTP_DIGITS', 6))
"""Number of digits in the TOTP code."""

_TOTP_PERIOD = int(os.environ.get('IBEAM_TOTP_PERIOD', 30))
"""Time period (in seconds) for TOTP code rotation."""


def generate_totp(secret: str, digits: int = 6, period: int = 30) -> str:
    """Generate a TOTP code using RFC 6238 algorithm.

    Args:
        secret: Base32-encoded secret key.
        digits: Number of digits in the output code.
        period: Time step in seconds.

    Returns:
        Zero-padded TOTP code string.
    """
    key = base64.b32decode(secret.upper().replace(' ', ''))
    counter = int(time.time()) // period
    msg = struct.pack('>Q', counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack('>I', h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


class TotpTwoFaHandler(TwoFaHandler):
    """2FA handler that generates TOTP codes locally using a shared secret.

    This eliminates the need for manual 2FA input or an external 2FA server.
    Simply provide the Base32-encoded TOTP secret from IBKR's Secure Login System
    via the IBEAM_TOTP_SECRET environment variable.
    """

    def __init__(self,
                 secret: str = None,
                 digits: int = None,
                 period: int = None,
                 *args, **kwargs):
        self.secret = secret if secret is not None else _TOTP_SECRET
        self.digits = digits if digits is not None else _TOTP_DIGITS
        self.period = period if period is not None else _TOTP_PERIOD
        super().__init__(*args, **kwargs)

    def get_two_fa_code(self, driver) -> Optional[str]:
        if not self.secret:
            _LOGGER.error(
                'TOTP secret is not set. '
                'Set the IBEAM_TOTP_SECRET environment variable with your '
                'Base32-encoded secret from IBKR Secure Login System.'
            )
            return None

        try:
            code = generate_totp(self.secret, self.digits, self.period)
            _LOGGER.info(f'TOTP code generated successfully ({self.digits} digits, {self.period}s period)')
            return code
        except Exception as e:
            _LOGGER.error(f'Failed to generate TOTP code: {e}')
            return None

    def __str__(self):
        return f"TotpTwoFaHandler(digits={self.digits}, period={self.period})"
