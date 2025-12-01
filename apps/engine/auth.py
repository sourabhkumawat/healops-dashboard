from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
import os

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__ident="2b",  # Use bcrypt 2b variant
)

def truncate_password(password: str) -> str:
    """Truncate password to 72 bytes (bcrypt limit)"""
    if not password:
        return password
    # Encode to bytes and truncate to 72 bytes
    password_bytes = password.encode('utf-8')
    if len(password_bytes) <= 72:
        return password
    # Truncate to 72 bytes, ensuring we don't break UTF-8 characters
    truncated_bytes = password_bytes[:72]
    # Remove any incomplete UTF-8 sequences at the end
    while truncated_bytes and truncated_bytes[-1] & 0xC0 == 0x80:
        truncated_bytes = truncated_bytes[:-1]
    # Decode back to string
    return truncated_bytes.decode('utf-8', errors='ignore')

def verify_password(plain_password, hashed_password):
    """Verify a plain password against a hashed password."""
    if not plain_password or not hashed_password:
        return False
    if not isinstance(plain_password, str) or not isinstance(hashed_password, str):
        return False
    
    # Check if hash looks like a valid bcrypt hash (starts with $2a$, $2b$, or $2y$)
    if not hashed_password.startswith(('$2a$', '$2b$', '$2y$')):
        import logging
        logging.warning(f"Invalid bcrypt hash format: {hashed_password[:20]}...")
        return False
    
    # Truncate password to ensure it's within bcrypt's 72-byte limit
    truncated_password = truncate_password(plain_password)
    
    # Double-check the byte length (safety check)
    password_bytes = truncated_password.encode('utf-8')
    if len(password_bytes) > 72:
        # Force truncate if somehow still too long
        truncated_password = truncated_password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
    
    try:
        return pwd_context.verify(truncated_password, hashed_password)
    except ValueError as e:
        # Handle password length errors or invalid hash format
        import logging
        error_msg = str(e)
        if "cannot be longer than 72 bytes" in error_msg:
            # This shouldn't happen with our truncation, but handle it anyway
            logging.error(f"Password still too long after truncation: {len(password_bytes)} bytes")
            return False
        logging.error(f"Password verification ValueError: {error_msg}")
        return False
    except Exception as e:
        # Handle other verification errors
        import logging
        logging.error(f"Password verification error: {type(e).__name__}: {str(e)}")
        return False

def get_password_hash(password):
    return pwd_context.hash(truncate_password(password))

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str, credentials_exception):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        return email
    except JWTError:
        raise credentials_exception
