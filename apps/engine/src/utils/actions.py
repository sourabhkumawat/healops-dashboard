from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseAction(ABC):
    name: str
    description: str
    
    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        pass

class ActionRegistry:
    _actions = {}

    @classmethod
    def register(cls, action: BaseAction):
        cls._actions[action.name] = action

    @classmethod
    def get(cls, name: str) -> BaseAction:
        return cls._actions.get(name)
    
    @classmethod
    def list_actions(cls):
        return [{"name": name, "description": action.description} for name, action in cls._actions.items()]

# --- Concrete Actions ---

class RestartContainerAction(BaseAction):
    name = "restart_container"
    description = "Restarts a docker container or kubernetes pod."

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        service_name = params.get("service_name")
        # In a real app, we'd use Docker/K8s API here
        print(f"Executing Action: Restarting container for {service_name}...")
        return {"status": "success", "message": f"Container {service_name} restarted."}

class ClearCacheAction(BaseAction):
    name = "clear_cache"
    description = "Clears the Redis/Memcached cache for a service."

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        key_pattern = params.get("key_pattern", "*")
        print(f"Executing Action: Clearing cache for pattern {key_pattern}...")
        return {"status": "success", "message": f"Cache cleared for {key_pattern}."}

# Register Actions
ActionRegistry.register(RestartContainerAction())
ActionRegistry.register(ClearCacheAction())
