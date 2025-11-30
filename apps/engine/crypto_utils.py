"""
Encryption utilities for storing OAuth tokens securely.
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Generate or load encryption key
def get_encryption_key() -> bytes:
    """Get or generate encryption key from environment or derive from SECRET_KEY."""
    key_str = os.getenv("ENCRYPTION_KEY")
    if key_str:
        return key_str.encode()
    
    # Derive key from SECRET_KEY if ENCRYPTION_KEY not set
    secret = os.getenv("SECRET_KEY", "supersecretkey")
    salt = b'healops_salt_2024'  # In production, use a random salt stored securely
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return key

_fernet = Fernet(get_encryption_key())

def encrypt_token(token: str) -> str:
    """Encrypt a token for storage."""
    if not token:
        return ""
    return _fernet.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a stored token."""
    if not encrypted_token:
        return ""
    try:
        return _fernet.decrypt(encrypted_token.encode()).decode()
    except Exception:
        return ""

