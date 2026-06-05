#!/usr/bin/env python3
"""
ARTIFACT REGENERATOR
Regenerates artifacts that fail validation.

When an artifact fails validation:
1. Extract the error message
2. Create a regeneration prompt
3. Call the LLM to fix it
4. Validate again
5. Repeat until passing or max attempts

This ensures only working artifacts reach the user.
"""

import json
import os
from typing import Dict, Callable, Optional
from datetime import datetime
import sys

sys.path.insert(0, os.path.expanduser("~/.latti"))
from artifact_validator import ArtifactValidator


class ArtifactRegenerator:
    """Regenerates artifacts that fail validation."""
    
    def __init__(self, latti_home: str = None, max_iterations: int = 3):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.validator = ArtifactValidator(latti_home)
        self.max_iterations = max_iterations
        self.regeneration_log = []
        self.load_log()
    
    def load_log(self):
        """Load regeneration log from disk."""
        log_path = os.path.join(self.latti_home, "artifact_regeneration.jsonl")
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    self.regeneration_log = [json.loads(line) for line in f if line.strip()]
            except:
                self.regeneration_log = []
    
    def save_log(self):
        """Save regeneration log to disk."""
        log_path = os.path.join(self.latti_home, "artifact_regeneration.jsonl")
        with open(log_path, 'w') as f:
            for entry in self.regeneration_log:
                f.write(json.dumps(entry) + "\n")
    
    def create_regeneration_prompt(self, artifact: Dict, error_message: str) -> str:
        """
        Create a prompt to regenerate the artifact.
        """
        artifact_type = artifact.get("type", "unknown")
        artifact_id = artifact.get("id", "unknown")
        original_content = artifact.get("content", "")
        description = artifact.get("description", "")
        
        prompt = f"""The artifact '{artifact_id}' of type '{artifact_type}' failed validation.

Original description: {description}

Original content:
```
{original_content}
```

Validation error: {error_message}

Please fix the artifact to pass validation. Ensure:
1. The artifact is complete and correct
2. All required sections are present
3. The code runs without errors
4. The design is implementable

Return ONLY the fixed artifact content, no explanations."""
        
        return prompt
    
    def regenerate(self, artifact: Dict, error_message: str, 
                  llm_call_fn: Callable) -> Dict:
        """
        Regenerate an artifact using the LLM.
        
        Args:
            artifact: The artifact to regenerate
            error_message: The validation error
            llm_call_fn: Function to call the LLM
                        Should take (prompt) and return (response_text)
        
        Returns: Regenerated artifact
        """
        prompt = self.create_regeneration_prompt(artifact, error_message)
        
        # Call LLM to regenerate
        try:
            new_content = llm_call_fn(prompt)
            
            # Create new artifact
            new_artifact = artifact.copy()
            new_artifact["content"] = new_content
            new_artifact["regenerated"] = True
            new_artifact["regeneration_reason"] = error_message
            
            return new_artifact
        
        except Exception as e:
            # If regeneration fails, return original
            return artifact
    
    def iterate_until_valid(self, artifact: Dict, 
                           llm_call_fn: Callable) -> Dict:
        """
        Iterate on an artifact until it passes validation.
        
        Args:
            artifact: The artifact to validate and regenerate
            llm_call_fn: Function to call the LLM for regeneration
        
        Returns: Final artifact (valid or best attempt)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "artifact_id": artifact.get("id", "unknown"),
            "artifact_type": artifact.get("type", "unknown"),
            "iterations": 0,
            "final_valid": False,
            "errors": []
        }
        
        current_artifact = artifact.copy()
        
        for iteration in range(self.max_iterations):
            log_entry["iterations"] = iteration + 1
            
            # Validate
            is_valid, result = self.validator.validate_artifact(current_artifact)
            
            if is_valid:
                log_entry["final_valid"] = True
                self.regeneration_log.append(log_entry)
                self.save_log()
                return current_artifact
            
            # If this is the last iteration, give up
            if iteration == self.max_iterations - 1:
                log_entry["errors"] = result.get("errors", [])
                self.regeneration_log.append(log_entry)
                self.save_log()
                return current_artifact
            
            # Otherwise, regenerate
            error_message = "; ".join(result.get("errors", []))
            current_artifact = self.regenerate(current_artifact, error_message, llm_call_fn)
        
        self.regeneration_log.append(log_entry)
        self.save_log()
        return current_artifact
    
    def get_regeneration_stats(self) -> Dict:
        """Get regeneration statistics."""
        if not self.regeneration_log:
            return {"total": 0, "successful": 0, "failed": 0, "success_rate": 0, "avg_iterations": 0}
        
        successful = sum(1 for e in self.regeneration_log if e.get("final_valid", False))
        failed = len(self.regeneration_log) - successful
        avg_iterations = sum(e.get("iterations", 0) for e in self.regeneration_log) / len(self.regeneration_log) if self.regeneration_log else 0
        
        return {
            "total": len(self.regeneration_log),
            "successful": successful,
            "failed": failed,
            "success_rate": (successful / len(self.regeneration_log) * 100) if self.regeneration_log else 0,
            "avg_iterations": avg_iterations
        }
    
    def report(self) -> str:
        """Generate regeneration report."""
        stats = self.get_regeneration_stats()
        
        report = []
        report.append("\n" + "="*60)
        report.append("ARTIFACT REGENERATION REPORT")
        report.append("="*60)
        report.append(f"Total regenerations: {stats['total']}")
        report.append(f"Successful: {stats['successful']}")
        report.append(f"Failed: {stats['failed']}")
        report.append(f"Success rate: {stats['success_rate']:.1f}%")
        report.append(f"Avg iterations: {stats['avg_iterations']:.1f}")
        report.append("="*60)
        
        return "\n".join(report)


class ArtifactQualityGate:
    """
    Quality gate that ensures all artifacts are valid before reaching the user.
    """
    
    def __init__(self, latti_home: str = None):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.validator = ArtifactValidator(latti_home)
        self.regenerator = ArtifactRegenerator(latti_home)
    
    def process_artifact(self, artifact: Dict, 
                        llm_call_fn: Optional[Callable] = None) -> Dict:
        """
        Process an artifact through the quality gate.
        
        If valid, return as-is.
        If invalid and llm_call_fn provided, regenerate until valid.
        If invalid and no llm_call_fn, return with validation errors.
        """
        # Validate
        is_valid, result = self.validator.validate_artifact(artifact)
        
        if is_valid:
            return artifact
        
        # If no LLM function, return with errors
        if llm_call_fn is None:
            artifact["validation_errors"] = result.get("errors", [])
            return artifact
        
        # Otherwise, regenerate
        final_artifact = self.regenerator.iterate_until_valid(artifact, llm_call_fn)
        
        # Add validation result
        is_valid, result = self.validator.validate_artifact(final_artifact)
        final_artifact["validation_passed"] = is_valid
        if not is_valid:
            final_artifact["validation_errors"] = result.get("errors", [])
        
        return final_artifact


if __name__ == "__main__":
    # Example usage
    regenerator = ArtifactRegenerator()
    
    # Simulate an artifact that needs regeneration
    bad_artifact = {
        "id": "code_bad_1",
        "type": "code",
        "language": "python",
        "description": "A function to add two numbers",
        "content": "def add(a, b):\n    return a + b\nprint(add(2, 3)"  # Missing closing paren
    }
    
    print("Testing artifact regeneration...")
    print(f"Original artifact: {bad_artifact['content']}")
    
    # Validate (should fail)
    validator = ArtifactValidator()
    is_valid, result = validator.validate_artifact(bad_artifact)
    print(f"\nValidation result: {is_valid}")
    print(f"Errors: {result['errors']}")
    
    # Simulate LLM regeneration
    def mock_llm_call(prompt: str) -> str:
        # Just return a fixed version
        return "def add(a, b):\n    return a + b\nprint(add(2, 3))"
    
    print("\nRegenerating artifact...")
    regenerated = regenerator.regenerate(bad_artifact, result['errors'][0], mock_llm_call)
    print(f"Regenerated artifact: {regenerated['content']}")
    
    # Validate regenerated
    is_valid, result = validator.validate_artifact(regenerated)
    print(f"\nValidation result: {is_valid}")
    print(f"Errors: {result['errors']}")
    
    print(regenerator.report())
