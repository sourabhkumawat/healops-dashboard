import os
import re
import traceback
import inspect
from typing import Optional, Dict, Tuple, List, Any

# Regex for parsing stack traces
# Python traceback format:
# File "filename", line 123, in function_name
FILE_LINE_PATTERN = re.compile(r'File "([^"]+)", line (\d+), in (.+)')

def is_source_file(filepath: str) -> bool:
    """Check if the file path points to a source file."""
    if not filepath:
        return False
    return filepath.endswith('.py')

def filter_sdk_frames(stack: str) -> str:
    """Filter out SDK internal frames from stack traces."""
    if not stack:
        return ""

    lines = stack.split('\n')
    filtered = []

    # Patterns to identify SDK internal frames
    sdk_patterns = [
        "healops_opentelemetry",
        "HealOpsLogger",
        "_send_log",
        "requests",
        "urllib3"
    ]

    for line in lines:
        if any(pattern in line for pattern in sdk_patterns):
            continue
        filtered.append(line)

    return '\n'.join(filtered)

def clean_stack_trace(stack: Optional[str]) -> Optional[str]:
    """Clean stack trace by filtering SDK frames."""
    if not stack:
        return None
    return filter_sdk_frames(stack)

def extract_file_path_from_stack(stack: str) -> Optional[str]:
    """Extract the first meaningful file path from a stack trace."""
    if not stack:
        return None

    lines = stack.split('\n')
    for line in lines:
        match = FILE_LINE_PATTERN.search(line)
        if match:
            filepath = match.group(1)
            # Skip SDK files
            if "healops_opentelemetry" in filepath:
                continue
            return filepath

    return None

def get_caller_info() -> Dict[str, Any]:
    """
    Extract caller information from the current stack.
    Skips SDK internal frames to find the actual user code.
    """
    try:
        # Get the current stack
        stack = inspect.stack()

        # Iterate through frames to find the first one outside the SDK
        for frame_info in stack:
            filepath = frame_info.filename

            # Skip SDK internal files
            if "healops_opentelemetry" in filepath or "logging" in filepath:
                continue

            # Skip standard library/site-packages if desirable,
            # but usually we just want to skip our own package.
            # For now, we assume anything not in our package is "user code"
            # or at least the call site we care about.

            return {
                "filePath": filepath,
                "line": frame_info.lineno,
                "functionName": frame_info.function,
                # We can construct a partial stack trace if needed, but inspect.stack() is heavy
                # "fullStack": "".join(traceback.format_stack())
            }

        return {}
    except Exception:
        return {}
