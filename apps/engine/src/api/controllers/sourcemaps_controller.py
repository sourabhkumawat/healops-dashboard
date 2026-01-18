"""
Source Maps Controller - Handles source map uploads.
"""
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

from src.database.models import SourceMap
from src.api.controllers.base import get_user_id_from_request


class SourceMapFile(BaseModel):
    file_path: str
    source_map: str  # Base64 encoded source map


class SourceMapUploadRequest(BaseModel):
    service_name: str
    release: str
    environment: str = "production"
    files: List[SourceMapFile]


class SourceMapsController:
    """Controller for source map uploads."""
    
    @staticmethod
    async def upload_sourcemaps(request: SourceMapUploadRequest, http_request: Request, db: Session):
        """
        Upload source maps for a service/release/environment combination.
        Requires API key authentication via X-HealOps-Key header.
        Optimized for bulk uploads with batch processing.
        """
        import base64
        import json
        
        try:
            # API Key is already validated by middleware
            api_key = http_request.state.api_key
            user_id = api_key.user_id
            
            # Bulk fetch existing source maps in one query
            file_paths = [f.file_path for f in request.files]
            existing_maps = {
                sm.file_path: sm 
                for sm in db.query(SourceMap).filter(
                    SourceMap.user_id == user_id,
                    SourceMap.service_name == request.service_name,
                    SourceMap.release == request.release,
                    SourceMap.environment == request.environment,
                    SourceMap.file_path.in_(file_paths)
                ).all()
            }
            
            # Process files in batches for better performance
            uploaded_count = 0
            skipped_count = 0
            new_source_maps = []
            
            for file_data in request.files:
                # Decode base64 source map
                try:
                    source_map_content = base64.b64decode(file_data.source_map).decode('utf-8')
                    # Validate it's valid JSON (quick validation)
                    json.loads(source_map_content)
                except Exception as e:
                    # Skip invalid source maps but continue with others
                    print(f"Warning: Invalid source map for {file_data.file_path}: {e}")
                    skipped_count += 1
                    continue
                
                # Check if source map already exists
                if file_data.file_path in existing_maps:
                    # Update existing source map
                    existing_maps[file_data.file_path].source_map = source_map_content
                else:
                    # Create new source map (add to batch)
                    source_map = SourceMap(
                        user_id=user_id,
                        service_name=request.service_name,
                        release=request.release,
                        environment=request.environment,
                        file_path=file_data.file_path,
                        source_map=source_map_content
                    )
                    new_source_maps.append(source_map)
                
                uploaded_count += 1
            
            # Bulk insert new source maps
            if new_source_maps:
                db.add_all(new_source_maps)
            
            # Commit all changes at once
            db.commit()
            
            return {
                "success": True,
                "files_uploaded": uploaded_count,
                "files_skipped": skipped_count,
                "release_id": request.release,
                "message": f"Successfully uploaded {uploaded_count} source maps"
            }
            
        except Exception as e:
            db.rollback()
            print(f"Error uploading source maps: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
