"""
Universal Agent integration for VMs and bare metal.
"""
from typing import Dict, Any
import os

class AgentIntegration:
    """Handles universal agent installation."""
    
    def __init__(self):
        self.base_url = os.getenv("BASE_URL", "http://localhost:8000")
    
    def get_install_script_url(self) -> str:
        """Get URL to install script."""
        return f"{self.base_url}/templates/install.sh"
    
    def get_install_command(self, api_key: str, endpoint: str) -> str:
        """
        Get one-line install command.
        
        Args:
            api_key: User's HealOps API key
            endpoint: HealOps endpoint URL
            
        Returns:
            Curl command to install agent
        """
        return f"""curl -s {self.get_install_script_url()} | \\
  sudo HEALOPS_API_KEY="{api_key}" \\
  HEALOPS_ENDPOINT="{endpoint}" \\
  bash"""
    
    def get_windows_install_command(self, api_key: str, endpoint: str) -> str:
        """Get PowerShell install command for Windows."""
        return f"""Invoke-WebRequest -Uri "{self.base_url}/templates/install.ps1" -OutFile install.ps1
.\\install.ps1 -ApiKey "{api_key}" -Endpoint "{endpoint}" """
    
    def verify_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Verify agent is connected and sending data.
        
        Args:
            agent_id: Unique agent identifier
            
        Returns:
            Verification status
        """
        # TODO: Implement actual verification
        # Check for recent heartbeat
        return {
            "status": "verified",
            "agent_id": agent_id,
            "last_heartbeat": None,
            "logs_received": 0
        }
    
    def register_agent(self, hostname: str, os_type: str, api_key_hash: str) -> Dict[str, Any]:
        """
        Register a new agent.
        
        Args:
            hostname: Agent hostname
            os_type: Operating system type
            api_key_hash: Hash of API key used
            
        Returns:
            Registration details
        """
        # TODO: Store in database
        return {
            "agent_id": f"agent_{hostname}",
            "registered_at": None,
            "status": "registered"
        }
