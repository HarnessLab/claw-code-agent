"""
Priority Router: Layer 4 Enforcement

After finishing a task, automatically identify and inject the next priority
into the prompt. This prevents the "what next?" routing pattern by making
the next action explicit BEFORE response generation.

The router runs BEFORE the LLM turn, not after. It reads:
  - Task list (actionable items)
  - Git status (uncommitted changes, branches)
  - Memory (scars, decisions, patterns)
  - Recent work (what was just completed)

Then it injects a directive: "Your next priority is X. Start working on it."
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class Priority:
    """Represents a next priority to work on."""
    
    type: str  # "task" | "git" | "memory" | "scar"
    title: str
    description: str
    urgency: float  # 0.0 to 1.0
    reason: str  # Why this is next
    
    def to_directive(self) -> str:
        """Convert to a system prompt directive."""
        return (
            f"**NEXT PRIORITY ({self.type.upper()}):** {self.title}\n"
            f"{self.description}\n"
            f"Reason: {self.reason}\n"
            f"Start working on this immediately. Do not ask for permission."
        )


class PriorityRouter:
    """Identifies and injects the next priority before response generation."""
    
    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()
        self.memory_dir = Path.home() / ".latti" / "memory"
        self.task_file = self.memory_dir / "tasks.json"
    
    def find_next_priority(self) -> Optional[Priority]:
        """Scan all sources and return the highest-urgency next priority.
        
        Returns None if no actionable priority found (silence is acceptable).
        """
        candidates: list[Priority] = []
        
        # Check task list
        task_priority = self._check_task_list()
        if task_priority:
            candidates.append(task_priority)
        
        # Check git status
        git_priority = self._check_git_status()
        if git_priority:
            candidates.append(git_priority)
        
        # Check memory for scars that need action
        scar_priority = self._check_memory_scars()
        if scar_priority:
            candidates.append(scar_priority)
        
        if not candidates:
            return None
        
        # Return highest urgency
        candidates.sort(key=lambda p: p.urgency, reverse=True)
        return candidates[0]
    
    def _check_task_list(self) -> Optional[Priority]:
        """Check for actionable tasks in the task list."""
        try:
            if not self.task_file.exists():
                return None
            
            with open(self.task_file) as f:
                tasks = json.load(f)
            
            # Find first actionable task (status = "ready" or "blocked" with resolved deps)
            for task in tasks.get("tasks", []):
                if task.get("status") == "ready":
                    return Priority(
                        type="task",
                        title=task.get("title", "Unnamed task"),
                        description=task.get("description", ""),
                        urgency=self._urgency_from_priority(task.get("priority", "medium")),
                        reason=f"Task is ready to start. Owner: {task.get('owner', 'unassigned')}",
                    )
        except Exception:
            pass
        
        return None
    
    def _check_git_status(self) -> Optional[Priority]:
        """Check for uncommitted changes that should be committed."""
        try:
            # Run git status
            result = os.popen("cd {} && git status --porcelain 2>/dev/null".format(
                self.workspace_root
            )).read().strip()
            
            if not result:
                return None
            
            # Count changes
            lines = result.split("\n")
            modified = len([l for l in lines if l.startswith(" M")])
            added = len([l for l in lines if l.startswith("A ")])
            deleted = len([l for l in lines if l.startswith(" D")])
            
            if modified + added + deleted == 0:
                return None
            
            return Priority(
                type="git",
                title="Commit staged changes",
                description=(
                    f"Uncommitted changes: {modified} modified, "
                    f"{added} added, {deleted} deleted"
                ),
                urgency=0.7,
                reason="Work is staged but not committed. Commit to preserve progress.",
            )
        except Exception:
            pass
        
        return None
    
    def _check_memory_scars(self) -> Optional[Priority]:
        """Check memory for scars that indicate next actions."""
        try:
            if not self.memory_dir.exists():
                return None
            
            # Look for scars with "action_required" or "next_step" markers
            for scar_file in self.memory_dir.glob("scar_*.md"):
                content = scar_file.read_text()
                
                # Check for action markers
                if "## NEXT PHASE" in content or "## ACTION REQUIRED" in content:
                    # Extract the action
                    match = re.search(
                        r"## (?:NEXT PHASE|ACTION REQUIRED)\n\n(.+?)(?:\n##|$)",
                        content,
                        re.DOTALL
                    )
                    if match:
                        action = match.group(1).strip()
                        return Priority(
                            type="scar",
                            title=f"Follow up on {scar_file.stem}",
                            description=action,
                            urgency=0.8,
                            reason="A scar indicates a follow-up action is needed.",
                        )
        except Exception:
            pass
        
        return None
    
    def _urgency_from_priority(self, priority_str: str) -> float:
        """Convert priority string to urgency float."""
        mapping = {
            "critical": 1.0,
            "high": 0.8,
            "medium": 0.5,
            "low": 0.3,
        }
        return mapping.get(priority_str.lower(), 0.5)
    
    def inject_priority_into_prompt(
        self,
        system_prompt: str,
        priority: Optional[Priority] = None,
    ) -> str:
        """Inject the next priority into the system prompt.
        
        If priority is None, finds it automatically.
        Returns the modified system prompt.
        """
        if priority is None:
            priority = self.find_next_priority()
        
        if priority is None:
            # No priority found; return unchanged
            return system_prompt
        
        # Inject at the end of the system prompt, before any user context
        directive = priority.to_directive()
        
        # Find a good insertion point (after system instructions, before context)
        if "---" in system_prompt:
            # Insert after the last --- separator
            parts = system_prompt.rsplit("---", 1)
            return parts[0] + "---\n\n" + directive + "\n\n" + parts[1]
        else:
            # Just append
            return system_prompt + "\n\n" + directive
