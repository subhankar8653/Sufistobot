#@suhanibots
from config import LOGGER
#@suhanibots
log = LOGGER(__name__)
#@suhanibots

def encrypt_token(token: str) -> str:
    """Store token as-is (no encryption)."""
    return token

#@suhanibots
def decrypt_token(encrypted: str) -> str:
    """Return token as-is (no decryption)."""
    return encrypted

#@suhanibots
def mask_token(token: str) -> str:
    """
    Mask a bot token for display purposes.
    Example: '123456:AAH...' → '123456:****...Xyz'
    """
    if not token or ":" not in token:
        return "****"
    parts = token.split(":", 1)
    bot_id_part = parts[0]
    secret = parts[1] if len(parts) > 1 else ""
    if len(secret) > 4:
        masked = f"{bot_id_part}:****...{secret[-4:]}"
    else:
        masked = f"{bot_id_part}:****"
    return masked
#@suhanibots

def mask_api_key(key: str) -> str:
    """
    Mask an API key for display purposes.
    Example: 'abcdef123456' → '****3456'
    """
    if not key:
        return "Not set"
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"
