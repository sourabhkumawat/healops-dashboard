"""
Custom CocoIndex source adapter for GitHub repositories.
Provides GitHub file tree and content access for CocoIndex indexing flows.
"""
import asyncio
from typing import NamedTuple, Optional, AsyncIterator
from dataclasses import dataclass
from datetime import datetime
import os

import cocoindex
from cocoindex.op import (
    SourceSpec,
    source_connector,
    SourceReadOptions,
    PartialSourceRow,
    PartialSourceRowData,
    NON_EXISTENCE,
    NO_ORDINAL,
)

from src.integrations.github.integration import GithubIntegration


class GitHubSource(SourceSpec):
    """Configuration for GitHub repository source."""
    repo_name: str
    ref: str = "main"
    # Store integration_id to recreate GithubIntegration
    integration_id: Optional[int] = None


class FileKey(NamedTuple):
    """Key type for files: path is the unique identifier."""
    path: str


@dataclass
class FileValue:
    """Value type for file content."""
    content: str
    path: str
    updated_at: Optional[datetime] = None


@source_connector(
    spec_cls=GitHubSource,
    key_type=FileKey,
    value_type=FileValue,
)
class GitHubSourceConnector:
    """CocoIndex connector for GitHub repositories."""
    
    def __init__(self, spec: GitHubSource):
        self.spec = spec
        self.repo_name = spec.repo_name
        self.ref = spec.ref
        self.integration_id = spec.integration_id
        self._github_integration: Optional[GithubIntegration] = None
        self._file_list: Optional[list[str]] = None
    
    @staticmethod
    def create(spec: GitHubSource) -> "GitHubSourceConnector":
        """
        Create connector instance.
        Note: GithubIntegration is initialized lazily in list() method.
        """
        return GitHubSourceConnector(spec)
    
    def _get_github_integration(self) -> GithubIntegration:
        """Get or create GithubIntegration instance."""
        if self._github_integration is None:
            if self.integration_id:
                self._github_integration = GithubIntegration(integration_id=self.integration_id)
            else:
                raise ValueError("integration_id is required to create GithubIntegration")
        return self._github_integration
    
    async def list(
        self,
        options: SourceReadOptions,
    ) -> AsyncIterator[PartialSourceRow[FileKey, FileValue]]:
        """
        List all files in the repository.
        
        Filters for code files (skips binary, excludes node_modules, etc.)
        """
        if self._file_list is None:
            # Get file list from GitHub (this is synchronous, so we'll call it)
            gh = self._get_github_integration()
            
            # Get repository structure (filters out node_modules, etc. internally)
            all_files = gh.get_repo_structure(
                repo_name=self.repo_name,
                path="",
                ref=self.ref,
                max_depth=10  # Deep enough to get all files
            )
            
            # Filter for code files only
            code_extensions = {
                '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.cpp', '.c', '.h',
                '.cs', '.rb', '.php', '.swift', '.kt', '.scala', '.clj', '.sh', '.yaml', '.yml',
                '.json', '.md', '.txt', '.sql', '.r', '.m', '.mm', '.pl', '.pm', '.lua'
            }
            
            self._file_list = [
                f for f in all_files
                if any(f.endswith(ext) for ext in code_extensions)
                and not any(skip in f for skip in ['node_modules', '__pycache__', '.git'])
            ]
        
        # Yield file keys
        for file_path in self._file_list:
            key = FileKey(path=file_path)
            data = PartialSourceRowData[FileValue]()
            
            # We don't have reliable timestamps from GitHub tree API without extra calls
            # So we skip ordinal for now (CocoIndex can use content fingerprinting)
            
            yield PartialSourceRow(key=key, data=data)
    
    async def get_value(
        self,
        key: FileKey,
        options: SourceReadOptions,
    ) -> PartialSourceRowData[FileValue]:
        """
        Fetch file content from GitHub.
        """
        gh = self._get_github_integration()
        
        content = gh.get_file_contents(
            repo_name=self.repo_name,
            file_path=key.path,
            ref=self.ref
        )
        
        if content is None:
            return PartialSourceRowData(
                value=NON_EXISTENCE,
                ordinal=NO_ORDINAL,
                content_version_fp=None,
            )
        
        value = FileValue(
            content=content,
            path=key.path,
            updated_at=None,  # We could fetch commit info, but it's expensive
        )
        
        data = PartialSourceRowData[FileValue](value=value)
        
        # Use content hash as fingerprint for change detection
        if options.include_content_version_fp:
            import hashlib
            data.content_version_fp = hashlib.sha256(content.encode()).digest()
        
        return data
    
    def provides_ordinal(self) -> bool:
        """We don't provide ordinal (would require extra API calls per file)."""
        return False
