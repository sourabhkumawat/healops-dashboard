"""
Database retry utility with exponential backoff for handling connection failures.
"""
import time
import logging
from typing import Callable, TypeVar, Optional, Any
from functools import wraps
from sqlalchemy.exc import OperationalError, DisconnectionError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF = 0.5  # seconds
MAX_BACKOFF = 5.0  # seconds
BACKOFF_MULTIPLIER = 2.0


def is_retryable_db_error(error: Exception) -> bool:
    """
    Check if a database error is retryable.
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error is retryable, False otherwise
    """
    if isinstance(error, (OperationalError, DisconnectionError)):
        error_str = str(error).lower()
        # Check for connection-related errors
        retryable_keywords = [
            "server closed the connection",
            "connection unexpectedly",
            "connection reset",
            "connection lost",
            "connection timed out",
            "could not connect",
            "connection refused",
            "broken pipe"
        ]
        return any(keyword in error_str for keyword in retryable_keywords)
    return False


def retry_db_operation(
    max_retries: int = MAX_RETRIES,
    initial_backoff: float = INITIAL_BACKOFF,
    max_backoff: float = MAX_BACKOFF,
    backoff_multiplier: float = BACKOFF_MULTIPLIER
):
    """
    Decorator for retrying database operations with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_backoff: Initial backoff time in seconds
        max_backoff: Maximum backoff time in seconds
        backoff_multiplier: Multiplier for exponential backoff
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            backoff = initial_backoff
            last_error = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    
                    # Check if error is retryable
                    if not is_retryable_db_error(e):
                        # Non-retryable error, raise immediately
                        logger.error(
                            f"Non-retryable database error in {func.__name__}: {e}",
                            exc_info=e
                        )
                        raise
                    
                    # If this is the last attempt, raise the error
                    if attempt >= max_retries:
                        logger.error(
                            f"Database operation {func.__name__} failed after {max_retries + 1} attempts: {e}",
                            exc_info=e
                        )
                        raise
                    
                    # Log retry attempt
                    logger.warning(
                        f"Database operation {func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {backoff:.2f}s..."
                    )
                    
                    # Wait before retrying
                    time.sleep(backoff)
                    
                    # Increase backoff for next retry (exponential backoff)
                    backoff = min(backoff * backoff_multiplier, max_backoff)
                    
                    # Refresh database session if provided
                    if args and isinstance(args[0], Session):
                        try:
                            args[0].rollback()
                            # Try to refresh the connection
                            args[0].execute("SELECT 1")
                        except Exception:
                            # If refresh fails, the next retry will handle it
                            pass
            
            # Should never reach here, but just in case
            if last_error:
                raise last_error
            
        return wrapper
    return decorator


def execute_with_retry(
    db: Session,
    operation: Callable[[], T],
    max_retries: int = MAX_RETRIES,
    operation_name: str = "database operation"
) -> T:
    """
    Execute a database operation with retry logic.
    
    Args:
        db: Database session
        operation: Callable that performs the database operation
        max_retries: Maximum number of retry attempts
        operation_name: Name of the operation for logging
        
    Returns:
        Result of the operation
    """
    backoff = INITIAL_BACKOFF
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except Exception as e:
            last_error = e
            
            # Check if error is retryable
            if not is_retryable_db_error(e):
                logger.error(
                    f"Non-retryable database error in {operation_name}: {e}",
                    exc_info=e
                )
                raise
            
            # If this is the last attempt, raise the error
            if attempt >= max_retries:
                logger.error(
                    f"Database operation {operation_name} failed after {max_retries + 1} attempts: {e}",
                    exc_info=e
                )
                raise
            
            # Log retry attempt
            logger.warning(
                f"Database operation {operation_name} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                f"Retrying in {backoff:.2f}s..."
            )
            
            # Rollback and wait before retrying
            try:
                db.rollback()
            except Exception:
                pass
            
            time.sleep(backoff)
            
            # Increase backoff for next retry
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
    
    # Should never reach here
    if last_error:
        raise last_error
