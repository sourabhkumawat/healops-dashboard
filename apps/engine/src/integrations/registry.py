"""
Integration Registry - Lists available integration providers.
"""
from typing import List, Dict, Any
from src.database.models import IntegrationProvider


class IntegrationRegistry:
    """Registry for available integration providers."""
    
    @staticmethod
    def list_providers() -> Dict[str, Any]:
        """
        List all available integration providers.
        
        Returns:
            Dictionary with providers list and metadata
        """
        providers = [
            {
                "id": "github",
                "name": "GitHub",
                "provider": IntegrationProvider.GITHUB.value,
                "description": "Connect GitHub to create pull requests and manage code",
                "icon": "github",
                "oauth": True,
                "api_key": False
            },
            {
                "id": "linear",
                "name": "Linear",
                "provider": IntegrationProvider.LINEAR.value,
                "description": "Connect Linear to create issues for incidents and link tickets to branches",
                "icon": "linear",
                "oauth": True,
                "api_key": False
            }
        ]
        
        return {
            "providers": providers,
            "count": len(providers)
        }
    
    @staticmethod
    def get_provider_by_id(provider_id: str) -> Dict[str, Any] | None:
        """
        Get provider information by ID.
        
        Args:
            provider_id: Provider ID (e.g., "github", "linear")
            
        Returns:
            Provider dictionary or None if not found
        """
        providers = IntegrationRegistry.list_providers()["providers"]
        for provider in providers:
            if provider["id"] == provider_id:
                return provider
        return None
    
    @staticmethod
    def get_provider_by_name(provider_name: str) -> Dict[str, Any] | None:
        """
        Get provider information by provider enum name.
        
        Args:
            provider_name: Provider name (e.g., "GITHUB", "LINEAR")
            
        Returns:
            Provider dictionary or None if not found
        """
        providers = IntegrationRegistry.list_providers()["providers"]
        for provider in providers:
            if provider["provider"] == provider_name:
                return provider
        return None
