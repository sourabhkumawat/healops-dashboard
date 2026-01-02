import json
import os
from typing import Dict, Any, List

MEMORY_FILE = "code_memory.json"

class CodeMemory:
    def __init__(self, storage_file: str = MEMORY_FILE):
        self.storage_file = storage_file
        self.memory_store: Dict[str, Any] = self._load_memory()

    def _load_memory(self) -> Dict[str, Any]:
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {"errors": {}, "fixes": {}, "context": {}}
        return {"errors": {}, "fixes": {}, "context": {}}

    def _save_memory(self):
        with open(self.storage_file, 'w') as f:
            json.dump(self.memory_store, f, indent=2)

    def store_error_context(self, error_signature: str, context: str):
        """Stores context related to a specific error signature."""
        if "errors" not in self.memory_store:
            self.memory_store["errors"] = {}

        if error_signature not in self.memory_store["errors"]:
            self.memory_store["errors"][error_signature] = []

        self.memory_store["errors"][error_signature].append({
            "context": context,
            "timestamp": "now" # In real app, use datetime
        })
        self._save_memory()

    def store_fix(self, error_signature: str, fix_description: str, code_patch: str):
        """Stores a successful fix for an error."""
        if "fixes" not in self.memory_store:
            self.memory_store["fixes"] = {}

        if error_signature not in self.memory_store["fixes"]:
            self.memory_store["fixes"][error_signature] = []

        self.memory_store["fixes"][error_signature].append({
            "description": fix_description,
            "patch": code_patch
        })
        self._save_memory()

    def retrieve_context(self, error_signature: str) -> Dict[str, Any]:
        """Retrieves past errors and fixes for a given error signature."""
        return {
            "past_errors": self.memory_store.get("errors", {}).get(error_signature, []),
            "known_fixes": self.memory_store.get("fixes", {}).get(error_signature, [])
        }

    def update_repo_context(self, file_path: str, summary: str):
        """Updates the memory with a summary of a file."""
        if "context" not in self.memory_store:
            self.memory_store["context"] = {}

        self.memory_store["context"][file_path] = summary
        self._save_memory()
