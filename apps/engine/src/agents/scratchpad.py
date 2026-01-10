"""
Scratchpad System for persistent file-based progress tracking.
Manus-style todo.md and notes.txt files for recovery and tracking.
"""
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from datetime import datetime
import os

if TYPE_CHECKING:
    from src.agents.workspace import Workspace

class Scratchpad:
    """
    Manus-style scratchpad for persistent file-based progress tracking.
    
    Manages two files:
    - healops_scratchpad_{incident_id}.md: Plan and progress
    - healops_notes_{incident_id}.txt: Notes and observations
    """
    
    def __init__(self, incident_id: int, github_integration=None, repo_name: str = None):
        """
        Initialize scratchpad.
        
        Args:
            incident_id: ID of the incident
            github_integration: Optional GitHub integration for storing in repo
            repo_name: Optional repository name in format "owner/repo"
        """
        self.incident_id = incident_id
        self.gh = github_integration
        self.repo_name = repo_name
        self.scratchpad_filename = f"healops_scratchpad_{incident_id}.md"
        self.notes_filename = f"healops_notes_{incident_id}.txt"
        self.use_github = github_integration is not None and repo_name is not None
        self.local_dir = os.getenv("SCRATCHPAD_DIR", "/tmp/healops_scratchpads")
        
        # Create local directory if it doesn't exist
        if not self.use_github and not os.path.exists(self.local_dir):
            os.makedirs(self.local_dir, exist_ok=True)
    
    def initialize(self, plan: List[Dict[str, Any]]):
        """
        Initialize scratchpad with plan.
        
        Args:
            plan: List of plan steps
        """
        from src.core.task_planner import TaskPlanner
        
        # Create a temporary planner to generate todo.md format
        # We'll use a mock planner just for formatting
        class MockPlanner:
            def __init__(self, plan):
                self.plan = plan
                self.incident_id = self.incident_id if hasattr(self, 'incident_id') else 0
            
            def to_todo_md(self):
                lines = ["# Fix Plan", "", f"Incident ID: {self.incident_id}", ""]
                lines.append("## Steps")
                lines.append("")
                for step in self.plan:
                    status_icon = {
                        "pending": "⬜",
                        "in_progress": "🔄",
                        "completed": "✅",
                        "failed": "❌",
                        "skipped": "⏭️"
                    }.get(step.get("status", "pending"), "⬜")
                    
                    lines.append(f"{status_icon} **Step {step.get('step_number', '?')}**: {step.get('description', 'N/A')}")
                    if step.get("files_to_read"):
                        lines.append(f"   📁 Files: {', '.join(step['files_to_read'])}")
                    lines.append("")
                return "\n".join(lines)
        
        mock_planner = MockPlanner(plan)
        todo_content = mock_planner.to_todo_md()
        
        # Write scratchpad file
        self._write_file(self.scratchpad_filename, todo_content)
        
        # Initialize notes file
        self._write_file(self.notes_filename, f"# Notes for Incident #{self.incident_id}\n\n")
    
    def update_progress(self, step_number: int, status: str, result: Optional[str] = None):
        """
        Update step status and result in scratchpad.
        
        Args:
            step_number: Step number to update
            status: New status
            result: Optional result text
        """
        # Read current scratchpad
        content = self._read_file(self.scratchpad_filename)
        if not content:
            return
        
        # Update step in content
        lines = content.split("\n")
        updated_lines = []
        in_step = False
        
        for line in lines:
            if f"**Step {step_number}**:" in line:
                in_step = True
                # Update status icon
                status_icon = {
                    "pending": "⬜",
                    "in_progress": "🔄",
                    "completed": "✅",
                    "failed": "❌",
                    "skipped": "⏭️"
                }.get(status, "⬜")
                
                # Replace icon
                if line.startswith("⬜") or line.startswith("🔄") or line.startswith("✅") or line.startswith("❌") or line.startswith("⏭️"):
                    line = status_icon + line[1:]
                
                updated_lines.append(line)
            elif in_step and line.strip() and not line.startswith("   "):
                # Next step or section, stop updating
                in_step = False
                updated_lines.append(line)
            elif in_step and result and "Result:" not in line:
                # Add result if not already present
                updated_lines.append(line)
                if result:
                    result_preview = str(result)[:200]
                    updated_lines.append(f"   📝 Result: {result_preview}...")
            else:
                updated_lines.append(line)
        
        # Write updated content
        self._write_file(self.scratchpad_filename, "\n".join(updated_lines))
    
    def add_note(self, note: str, category: str = "general"):
        """
        Add a note to notes file.
        
        Args:
            note: Note text
            category: Note category
        """
        timestamp = datetime.utcnow().isoformat()
        note_line = f"[{timestamp}] [{category.upper()}] {note}\n"
        
        # Append to notes file
        current_notes = self._read_file(self.notes_filename) or ""
        self._write_file(self.notes_filename, current_notes + note_line)
    
    def sync_from_workspace(self, workspace: "Workspace"):
        """
        Sync scratchpad from workspace state.
        
        Args:
            workspace: Workspace instance to sync from
        """
        # Update plan progress
        if workspace.todo:
            for step in workspace.todo:
                status = step.get("status", "pending")
                result = step.get("result")
                step_number = step.get("step_number")
                if step_number:
                    self.update_progress(step_number, status, result)
        
        # Add notes from workspace
        for note_data in workspace.notes:
            self.add_note(note_data["note"], note_data.get("category", "general"))
    
    def read_scratchpad(self) -> Optional[str]:
        """
        Read scratchpad content.
        
        Returns:
            Scratchpad content or None
        """
        return self._read_file(self.scratchpad_filename)
    
    def read_notes(self) -> Optional[str]:
        """
        Read notes content.
        
        Returns:
            Notes content or None
        """
        return self._read_file(self.notes_filename)
    
    def _write_file(self, filename: str, content: str):
        """
        Write file to GitHub or local filesystem.
        
        Args:
            filename: Filename
            content: Content to write
        """
        if self.use_github:
            try:
                # Write to GitHub as a temporary file
                # Use a branch or commit directly to a temp location
                # For now, we'll store in a .healops/ directory
                path = f".healops/{filename}"
                self.gh.create_or_update_file(
                    self.repo_name,
                    path,
                    content,
                    f"Update Healops scratchpad for incident {self.incident_id}"
                )
            except Exception as e:
                print(f"Warning: Failed to write scratchpad to GitHub: {e}")
                # Fallback to local
                self._write_file_local(filename, content)
        else:
            self._write_file_local(filename, content)
    
    def _write_file_local(self, filename: str, content: str):
        """Write file to local filesystem."""
        filepath = os.path.join(self.local_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    
    def _read_file(self, filename: str) -> Optional[str]:
        """
        Read file from GitHub or local filesystem.
        
        Args:
            filename: Filename
            
        Returns:
            File content or None
        """
        if self.use_github:
            try:
                path = f".healops/{filename}"
                return self.gh.get_file_contents(self.repo_name, path)
            except Exception as e:
                print(f"Warning: Failed to read scratchpad from GitHub: {e}")
                # Fallback to local
                return self._read_file_local(filename)
        else:
            return self._read_file_local(filename)
    
    def _read_file_local(self, filename: str) -> Optional[str]:
        """Read file from local filesystem."""
        filepath = os.path.join(self.local_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        return None
    
    def cleanup(self):
        """
        Clean up scratchpad files (optional, for cleanup after completion).
        """
        if self.use_github:
            try:
                # Delete from GitHub
                path = f".healops/{self.scratchpad_filename}"
                self.gh.delete_file(self.repo_name, path, f"Cleanup scratchpad for incident {self.incident_id}")
                
                path = f".healops/{self.notes_filename}"
                self.gh.delete_file(self.repo_name, path, f"Cleanup notes for incident {self.incident_id}")
            except Exception as e:
                print(f"Warning: Failed to cleanup scratchpad from GitHub: {e}")
        else:
            # Delete local files
            for filename in [self.scratchpad_filename, self.notes_filename]:
                filepath = os.path.join(self.local_dir, filename)
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except Exception as e:
                        print(f"Warning: Failed to delete local scratchpad file {filepath}: {e}")

