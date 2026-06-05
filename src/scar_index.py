"""
Scar Index: Persistent learning from session outcomes.

A scar is a structured record of a problem, the approach taken, and the outcome.
The scar index enables the agent to learn from past sessions and route future
problems to models/strategies that worked before.

Scars are stored as JSON in ~/.latti/scars/ and indexed for fast retrieval.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4


@dataclass
class Scar:
    """A record of a problem, approach, and outcome."""
    
    id: str
    problem_signature: str  # TF-IDF or embedding-based signature
    problem_description: str  # Human-readable description
    model_used: str  # e.g., "claude-sonnet-4.6", "openai/o1"
    cost: float  # Cost in dollars
    outcome: str  # "success", "failure", "partial"
    lesson: str  # What to do differently next time
    timestamp: str  # ISO 8601
    session_id: str  # Which session created this scar
    reasoning_tokens: int = 0  # If extended thinking was used
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @staticmethod
    def from_dict(d: dict) -> Scar:
        return Scar(**d)


class ScarIndex:
    """Manages scar storage and retrieval."""
    
    def __init__(self, scar_dir: Optional[str] = None):
        """Initialize scar index.
        
        Args:
            scar_dir: Directory to store scars. Defaults to ~/.latti/scars/
        """
        if scar_dir is None:
            scar_dir = os.path.expanduser("~/.latti/scars")
        
        self.scar_dir = Path(scar_dir)
        self.scar_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.scar_dir.parent / "scar_index.json"
        self._index = self._load_index()
    
    def _load_index(self) -> dict:
        """Load the scar index from disk."""
        if self.index_path.exists():
            try:
                with open(self.index_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save_index(self) -> None:
        """Save the scar index to disk."""
        with open(self.index_path, 'w') as f:
            json.dump(self._index, f, indent=2)
    
    def record_scar(
        self,
        problem_description: str,
        model_used: str,
        cost: float,
        outcome: str,
        lesson: str,
        session_id: str,
        reasoning_tokens: int = 0,
    ) -> Scar:
        """Record a new scar from a session outcome.
        
        Args:
            problem_description: What was the problem?
            model_used: Which model was used?
            cost: Cost in dollars
            outcome: "success", "failure", or "partial"
            lesson: What to do differently next time
            session_id: Which session created this scar
            reasoning_tokens: If extended thinking was used
            
        Returns:
            The created Scar object
        """
        scar_id = f"scar-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        
        # Create problem signature (simple: first 50 chars + outcome)
        problem_signature = f"{problem_description[:50]}:{outcome}"
        
        scar = Scar(
            id=scar_id,
            problem_signature=problem_signature,
            problem_description=problem_description,
            model_used=model_used,
            cost=cost,
            outcome=outcome,
            lesson=lesson,
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            reasoning_tokens=reasoning_tokens,
        )
        
        # Save scar to disk
        scar_file = self.scar_dir / f"{scar_id}.json"
        with open(scar_file, 'w') as f:
            json.dump(scar.to_dict(), f, indent=2)
        
        # Update index
        self._index[scar_id] = {
            "problem_signature": problem_signature,
            "model_used": model_used,
            "outcome": outcome,
            "timestamp": scar.timestamp,
            "file": str(scar_file),
        }
        self._save_index()
        
        return scar
    
    def find_similar_scars(
        self,
        problem_description: str,
        max_results: int = 5,
    ) -> list[Scar]:
        """Find scars similar to a given problem.
        
        Uses simple substring matching on problem description.
        For production, this should use TF-IDF or embeddings.
        
        Args:
            problem_description: The current problem
            max_results: Maximum number of scars to return
            
        Returns:
            List of similar scars, sorted by relevance
        """
        similar = []
        
        for scar_id, scar_meta in self._index.items():
            scar_file = Path(scar_meta["file"])
            if not scar_file.exists():
                continue
            
            try:
                with open(scar_file) as f:
                    scar_data = json.load(f)
                    scar = Scar.from_dict(scar_data)
                
                # Simple similarity: check if key words overlap
                problem_words = set(problem_description.lower().split())
                scar_words = set(scar.problem_description.lower().split())
                overlap = len(problem_words & scar_words)
                
                if overlap > 0:
                    similar.append((overlap, scar))
            except (json.JSONDecodeError, IOError, KeyError):
                continue
        
        # Sort by overlap (descending) and return top N
        similar.sort(key=lambda x: x[0], reverse=True)
        return [scar for _, scar in similar[:max_results]]
    
    def get_scar(self, scar_id: str) -> Optional[Scar]:
        """Get a specific scar by ID."""
        if scar_id not in self._index:
            return None
        
        scar_file = Path(self._index[scar_id]["file"])
        if not scar_file.exists():
            return None
        
        try:
            with open(scar_file) as f:
                return Scar.from_dict(json.load(f))
        except (json.JSONDecodeError, IOError):
            return None
    
    def list_scars(self, limit: int = 100) -> list[Scar]:
        """List all scars, most recent first."""
        scars = []
        
        for scar_id in sorted(self._index.keys(), reverse=True)[:limit]:
            scar = self.get_scar(scar_id)
            if scar:
                scars.append(scar)
        
        return scars
    
    def get_stats(self) -> dict:
        """Get statistics about scars."""
        scars = self.list_scars(limit=1000)
        
        if not scars:
            return {
                "total_scars": 0,
                "success_rate": 0.0,
                "total_cost": 0.0,
                "avg_cost": 0.0,
            }
        
        successes = sum(1 for s in scars if s.outcome == "success")
        total_cost = sum(s.cost for s in scars)
        
        return {
            "total_scars": len(scars),
            "success_rate": successes / len(scars),
            "total_cost": total_cost,
            "avg_cost": total_cost / len(scars),
            "by_model": self._stats_by_model(scars),
        }
    
    def _stats_by_model(self, scars: list[Scar]) -> dict:
        """Get statistics grouped by model."""
        by_model = {}
        
        for scar in scars:
            if scar.model_used not in by_model:
                by_model[scar.model_used] = {
                    "count": 0,
                    "successes": 0,
                    "total_cost": 0.0,
                }
            
            by_model[scar.model_used]["count"] += 1
            if scar.outcome == "success":
                by_model[scar.model_used]["successes"] += 1
            by_model[scar.model_used]["total_cost"] += scar.cost
        
        return by_model
