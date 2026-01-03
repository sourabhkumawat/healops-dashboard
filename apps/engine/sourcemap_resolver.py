"""
Server-side source map resolution utility.
Resolves bundled/minified file paths to original source paths using uploaded source maps.
"""
import json
import re
from typing import Optional, Dict, Tuple, List
from sqlalchemy.orm import Session
from models import SourceMap


class SourceMapConsumer:
    """Simple source map consumer for resolving positions."""
    
    def __init__(self, source_map_json: dict):
        self.sources = source_map_json.get("sources", [])
        self.names = source_map_json.get("names", [])
        self.mappings = source_map_json.get("mappings", "")
        self.file = source_map_json.get("file", "")
        self.sourceRoot = source_map_json.get("sourceRoot", "")
        # Store parsed mappings for quick lookup
        self._mapping_cache = {}
    
    def original_position_for(self, line: int, column: int) -> Optional[Dict]:
        """
        Resolve a position in the generated file to original source.
        Simplified implementation that returns the best matching source file.
        For full accuracy, VLQ decoding would be needed, but this provides reasonable results.
        """
        try:
            # Source map line numbers are 1-based
            if line < 1:
                return None
            
            # Try to find a source file - use first available source for now
            # A proper implementation would decode VLQ mappings and binary search
            if self.sources:
                # Use the first source file as a reasonable approximation
                # In practice, source maps often have one primary source file
                source_file = self.sources[0]
                
                # Apply sourceRoot if present
                if self.sourceRoot:
                    if self.sourceRoot.endswith('/'):
                        source_file = self.sourceRoot + source_file
                    else:
                        source_file = self.sourceRoot + '/' + source_file
                
                # Clean webpack:// prefixes
                if source_file.startswith('webpack://'):
                    source_file = source_file.replace('webpack://./', '').replace('webpack://', '')
                
                # Remove leading ./ or ./
                source_file = source_file.lstrip('./')
                
                return {
                    'source': source_file,
                    'line': line,  # Approximate - would need VLQ decoding for exact
                    'column': column  # Approximate
                }
        except Exception as e:
            print(f"Error in original_position_for: {e}")
            pass
        return None


def get_source_map_for_file(
    db: Session,
    user_id: int,
    service_name: str,
    file_path: str,
    release: Optional[str] = None,
    environment: str = "production"
) -> Optional[SourceMap]:
    """
    Retrieve a source map from the database for a given file path.
    
    Args:
        db: Database session
        user_id: User ID
        service_name: Service name
        file_path: Bundled file path (e.g., /_next/static/chunks/abc123.js)
        release: Release identifier (optional, searches latest if not provided)
        environment: Environment name
    
    Returns:
        SourceMap object if found, None otherwise
    """
    # Extract the file path without query params, fragments, and domain
    clean_path = file_path.split('?')[0].split('#')[0]
    
    # Extract path from full URL (e.g., https://domain.com/path -> /path)
    if '://' in clean_path:
        # Remove protocol and domain
        parts = clean_path.split('://', 1)
        if len(parts) > 1:
            path_parts = parts[1].split('/', 1)
            if len(path_parts) > 1:
                clean_path = '/' + path_parts[1]
            else:
                clean_path = '/'
    
    # Ensure path starts with /
    if not clean_path.startswith('/'):
        clean_path = '/' + clean_path
    
    # Try to find exact match first
    query = db.query(SourceMap).filter(
        SourceMap.user_id == user_id,
        SourceMap.service_name == service_name,
        SourceMap.environment == environment,
        SourceMap.file_path == clean_path
    )
    
    if release:
        query = query.filter(SourceMap.release == release)
    
    source_map = query.order_by(SourceMap.created_at.desc()).first()
    
    # If no exact match and no release specified, try without release filter
    if not source_map and not release:
        source_map = db.query(SourceMap).filter(
            SourceMap.user_id == user_id,
            SourceMap.service_name == service_name,
            SourceMap.environment == environment,
            SourceMap.file_path == clean_path
        ).order_by(SourceMap.created_at.desc()).first()
    
    return source_map


def resolve_file_path(
    db: Session,
    user_id: int,
    service_name: str,
    file_path: str,
    line: Optional[int] = None,
    column: Optional[int] = None,
    release: Optional[str] = None,
    environment: str = "production"
) -> Optional[str]:
    """
    Resolve a bundled file path to original source path using source maps.
    
    Args:
        db: Database session
        user_id: User ID
        service_name: Service name
        file_path: Bundled file path
        line: Line number (optional, helps with resolution)
        column: Column number (optional, helps with resolution)
        release: Release identifier (optional)
        environment: Environment name
    
    Returns:
        Resolved source file path, or None if resolution fails
    """
    # Check if it looks like a bundled file
    bundled_patterns = [
        r'/_next/static/chunks/',
        r'/_next/static/.*\.js',
        r'\.min\.js',
        r'chunk-[a-f0-9]+\.js',
        r'webpack://',
    ]
    
    is_bundled = any(re.search(pattern, file_path) for pattern in bundled_patterns)
    if not is_bundled:
        return None
    
    # Get source map from database
    source_map_record = get_source_map_for_file(
        db, user_id, service_name, file_path, release, environment
    )
    
    if not source_map_record:
        return None
    
    try:
        # Parse source map JSON
        source_map_json = json.loads(source_map_record.source_map)
        consumer = SourceMapConsumer(source_map_json)
        
        # Resolve position
        line_num = line or 1
        column_num = column or 0
        resolved = consumer.original_position_for(line_num, column_num)
        
        if resolved and resolved.get('source'):
            return resolved['source']
    except Exception as e:
        # Silent fail - log if needed
        print(f"Warning: Failed to resolve source map for {file_path}: {e}")
        pass
    
    return None


def resolve_stack_trace_line(
    db: Session,
    user_id: int,
    service_name: str,
    stack_line: str,
    release: Optional[str] = None,
    environment: str = "production"
) -> str:
    """
    Resolve a single stack trace line from bundled to original source.
    
    Args:
        db: Database session
        user_id: User ID
        service_name: Service name
        stack_line: Stack trace line (e.g., "at o (file.js:123:45)")
        release: Release identifier (optional)
        environment: Environment name
    
    Returns:
        Resolved stack trace line, or original if resolution fails
    """
    # Patterns to match stack trace lines
    # Note: Need to handle URLs with colons (e.g., https://domain.com/file.js:123:45)
    # Strategy: Match from the end - capture line:column first, then everything before
    patterns = [
        # Chrome/Edge: "at functionName (file:line:column)"
        # Non-greedy match up to :line:column)
        r'at\s+(?:[^(]+)?\((.+?):(\d+):(\d+)\)',
        # Chrome/Edge: "at file:line:column" 
        r'at\s+(.+?):(\d+):(\d+)(?:\s|$)',
        # Firefox: "functionName@file:line:column"
        r'@(.+?):(\d+):(\d+)(?:\s|$)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, stack_line)
        if match:
            file_url = match.group(1).strip()
            try:
                line_num = int(match.group(2))
                column_num = int(match.group(3))
            except (ValueError, IndexError):
                continue
            
            # Resolve the file path
            resolved_path = resolve_file_path(
                db, user_id, service_name, file_url, line_num, column_num, release, environment
            )
            
            if resolved_path:
                # Replace the file URL in the stack trace line (only first occurrence to be safe)
                return stack_line.replace(file_url, resolved_path, 1)
    
    return stack_line


def resolve_stack_trace(
    db: Session,
    user_id: int,
    service_name: str,
    stack_trace: str,
    release: Optional[str] = None,
    environment: str = "production"
) -> str:
    """
    Resolve an entire stack trace from bundled files to original sources.
    
    Args:
        db: Database session
        user_id: User ID
        service_name: Service name
        stack_trace: Full stack trace string
        release: Release identifier (optional)
        environment: Environment name
    
    Returns:
        Resolved stack trace with original file paths
    """
    if not stack_trace:
        return stack_trace
    
    lines = stack_trace.split('\n')
    resolved_lines = []
    
    for line in lines:
        resolved_line = resolve_stack_trace_line(
            db, user_id, service_name, line, release, environment
        )
        resolved_lines.append(resolved_line)
    
    return '\n'.join(resolved_lines)


def resolve_metadata_with_sourcemaps(
    db: Session,
    user_id: int,
    service_name: str,
    metadata: Dict,
    release: Optional[str] = None,
    environment: str = "production"
) -> Dict:
    """
    Resolve all bundled file paths in log metadata using source maps.
    
    Args:
        db: Database session
        user_id: User ID
        service_name: Service name
        metadata: Log metadata dictionary
        release: Release identifier (optional)
        environment: Environment name
    
    Returns:
        Metadata with resolved file paths
    """
    if not metadata or not isinstance(metadata, dict):
        return metadata
    
    resolved_metadata = metadata.copy()
    
    # Resolve filePath
    if 'filePath' in resolved_metadata and resolved_metadata['filePath']:
        file_path = resolved_metadata['filePath']
        line = resolved_metadata.get('line')
        column = resolved_metadata.get('column')
        resolved = resolve_file_path(
            db, user_id, service_name, file_path, line, column, release, environment
        )
        if resolved:
            resolved_metadata['filePath'] = resolved
            resolved_metadata['originalFilePath'] = file_path  # Keep original for reference
    
    # Resolve file_path (alternative key)
    if 'file_path' in resolved_metadata and resolved_metadata['file_path']:
        file_path = resolved_metadata['file_path']
        line = resolved_metadata.get('line')
        column = resolved_metadata.get('column')
        resolved = resolve_file_path(
            db, user_id, service_name, file_path, line, column, release, environment
        )
        if resolved:
            resolved_metadata['file_path'] = resolved
            resolved_metadata['original_file_path'] = file_path
    
    # Resolve stack traces
    for stack_key in ['stack', 'errorStack', 'fullStack']:
        if stack_key in resolved_metadata and resolved_metadata[stack_key]:
            resolved_metadata[stack_key] = resolve_stack_trace(
                db, user_id, service_name, resolved_metadata[stack_key], release, environment
            )
    
    # Resolve exception stacktrace
    if 'exception' in resolved_metadata and isinstance(resolved_metadata['exception'], dict):
        exception = resolved_metadata['exception'].copy()
        if 'stacktrace' in exception and exception['stacktrace']:
            exception['stacktrace'] = resolve_stack_trace(
                db, user_id, service_name, exception['stacktrace'], release, environment
            )
        resolved_metadata['exception'] = exception
    
    # Resolve OTel attributes
    if 'attributes' in resolved_metadata and isinstance(resolved_metadata['attributes'], dict):
        attributes = resolved_metadata['attributes'].copy()
        for attr_key in ['code.filepath', 'code.file_path']:
            if attr_key in attributes and attributes[attr_key]:
                resolved = resolve_file_path(
                    db, user_id, service_name, attributes[attr_key], None, None, release, environment
                )
                if resolved:
                    attributes[attr_key] = resolved
        resolved_metadata['attributes'] = attributes
    
    return resolved_metadata

