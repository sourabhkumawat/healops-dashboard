"""
Simple in-memory rate limiter for API endpoints.
Uses a sliding window approach with Redis or in-memory storage.
"""
from datetime import datetime, timedelta
from typing import Dict, Tuple
import time
import redis
import os

# Try to use Redis if available, otherwise fall back to in-memory
try:
    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=0,
        decode_responses=True
    )
    redis_client.ping()  # Test connection
    USE_REDIS = True
except (redis.ConnectionError, redis.TimeoutError, Exception):
    USE_REDIS = False
    # Fallback to in-memory storage
    _rate_limit_store: Dict[str, list] = {}
    print("⚠️  Redis not available, using in-memory rate limiting (not suitable for production with multiple workers)")


def check_rate_limit(
    key: str,
    max_requests: int = 10,
    window_seconds: int = 60
) -> Tuple[bool, int]:
    """
    Check if a request should be rate limited.
    
    Args:
        key: Unique identifier for the rate limit (e.g., "user_id:123" or "ip:1.2.3.4")
        max_requests: Maximum number of requests allowed in the window
        window_seconds: Time window in seconds
        
    Returns:
        Tuple of (is_allowed: bool, remaining_requests: int)
    """
    if USE_REDIS:
        return _check_rate_limit_redis(key, max_requests, window_seconds)
    else:
        return _check_rate_limit_memory(key, max_requests, window_seconds)


def _check_rate_limit_redis(
    key: str,
    max_requests: int,
    window_seconds: int
) -> Tuple[bool, int]:
    """Redis-based rate limiting using sorted sets."""
    try:
        now = time.time()
        window_start = now - window_seconds
        
        # Use Redis sorted set to track requests
        redis_key = f"rate_limit:{key}"
        
        # Remove old entries
        redis_client.zremrangebyscore(redis_key, 0, window_start)
        
        # Count current requests in window
        current_count = redis_client.zcard(redis_key)
        
        if current_count >= max_requests:
            # Calculate remaining time until oldest request expires
            oldest_request = redis_client.zrange(redis_key, 0, 0, withscores=True)
            if oldest_request:
                oldest_time = oldest_request[0][1]
                remaining_seconds = max(0, int(window_seconds - (now - oldest_time)))
            else:
                remaining_seconds = window_seconds
            return False, remaining_seconds
        
        # Add current request
        redis_client.zadd(redis_key, {str(now): now})
        redis_client.expire(redis_key, window_seconds)
        
        remaining = max_requests - current_count - 1
        return True, remaining
    except (redis.ConnectionError, redis.TimeoutError, redis.RedisError) as e:
        # If Redis fails, fall back to allowing the request (fail open)
        # Log the error but don't block the user
        print(f"⚠️  Redis rate limiting failed, allowing request: {e}")
        return True, max_requests - 1  # Return as if one request was used
    except Exception as e:
        # Catch any other unexpected errors
        print(f"⚠️  Unexpected error in rate limiting, allowing request: {e}")
        return True, max_requests - 1


def _check_rate_limit_memory(
    key: str,
    max_requests: int,
    window_seconds: int
) -> Tuple[bool, int]:
    """In-memory rate limiting (not suitable for production with multiple workers)."""
    now = datetime.now()
    window_start = now - timedelta(seconds=window_seconds)
    
    # Get or create request history for this key
    if key not in _rate_limit_store:
        _rate_limit_store[key] = []
    
    request_times = _rate_limit_store[key]
    
    # Remove old entries outside the window
    request_times[:] = [req_time for req_time in request_times if req_time > window_start]
    
    # Check if limit exceeded
    if len(request_times) >= max_requests:
        # Calculate remaining time until oldest request expires
        oldest_request = min(request_times) if request_times else now
        remaining_seconds = max(0, int((oldest_request + timedelta(seconds=window_seconds) - now).total_seconds()))
        return False, remaining_seconds
    
    # Add current request
    request_times.append(now)
    
    # Clean up old keys (prevent memory leak)
    if len(_rate_limit_store) > 10000:
        # Remove keys older than 1 hour
        cutoff = now - timedelta(hours=1)
        keys_to_remove = [
            k for k, times in _rate_limit_store.items()
            if not times or max(times) < cutoff
        ]
        for k in keys_to_remove:
            del _rate_limit_store[k]
    
    remaining = max_requests - len(request_times)
    return True, remaining

