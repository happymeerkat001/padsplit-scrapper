"""Conservative validation of provider attachment display metadata.

Names are display-only: they must never be used as storage paths or URLs.
Unsafe names are omitted rather than decoded or modified in a response.
"""
import re
import unicodedata
from urllib.parse import unquote

_CREDENTIAL_ASSIGNMENT = re.compile(
    r'(?i)(?:^|[\s&;,.\[\]_-])(?:access[_-]?token|refresh[_-]?token|auth[_-]?token|'
    r'token|secret|signature|sig|api[_-]?key|key|password|passwd|pwd|'
    r'session(?:[_-]?(?:id|token|key))?|authorization|bearer|cookie|credential|'
    r'client[_-]?secret|x[_-](?:amz|goog)[_-][a-z0-9_-]+)\s*='
)
_HOSTLIKE = re.compile(r'(?i)(?<![a-z0-9])www\.')


def safe_attachment_filename(value):
    """Validate all decoded forms without changing the returned filename.

    Literal percent characters and benign escapes remain usable. URL, path,
    control-character and credential-like forms, including nested escapes and
    Unicode compatibility variants, are excluded from ordinary API metadata.
    """
    if not isinstance(value,str) or len(value)>500:
        return False
    current=value
    for _ in range(6):
        current=unicodedata.normalize('NFKC',current)
        if (len(current)>500 or current in ('.','..') or
            any(ch in '/\\:?#' or unicodedata.category(ch) in ('Cc','Cf') for ch in current) or
            _CREDENTIAL_ASSIGNMENT.search(current) or _HOSTLIKE.search(current)):
            return False
        try:
            decoded=unquote(current,encoding='utf-8',errors='strict')
        except UnicodeDecodeError:
            return False
        if decoded==current:
            return True
        current=decoded
    # Excessively nested encoded text is ambiguous and fails closed.
    return False
