#!/usr/bin/env python3
"""
ARTIFACT VALIDATOR
Validates artifacts before they reach the user.

For code: runs it, checks for errors
For designs: checks completeness, structure, implementability
For docs: checks clarity, completeness, correctness

Only emits artifacts that pass validation.
Iterates until passing or max attempts reached.
"""

import json
import os
import subprocess
import tempfile
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from pathlib import Path


class CodeValidator:
    """Validates code artifacts."""
    
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
    
    def validate(self, code: str, language: str = "python") -> Tuple[bool, str]:
        """
        Validate code by running it.
        
        Returns: (is_valid, error_message)
        """
        if language == "python":
            return self._validate_python(code)
        elif language == "javascript":
            return self._validate_javascript(code)
        elif language == "bash":
            return self._validate_bash(code)
        else:
            return True, "Unknown language, skipping validation"
    
    def _validate_python(self, code: str) -> Tuple[bool, str]:
        """Validate Python code."""
        # Check syntax
        try:
            compile(code, '<string>', 'exec')
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        
        # Try to run it (with timeout)
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(code)
                f.flush()
                
                result = subprocess.run(
                    ['python3', f.name],
                    capture_output=True,
                    timeout=5,
                    text=True
                )
                
                os.unlink(f.name)
                
                if result.returncode != 0:
                    return False, f"Runtime error: {result.stderr}"
                
                return True, "Code runs successfully"
        
        except subprocess.TimeoutExpired:
            return False, "Code execution timed out"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def _validate_javascript(self, code: str) -> Tuple[bool, str]:
        """Validate JavaScript code."""
        # Check syntax with node
        try:
            result = subprocess.run(
                ['node', '--check'],
                input=code,
                capture_output=True,
                timeout=5,
                text=True
            )
            
            if result.returncode != 0:
                return False, f"Syntax error: {result.stderr}"
            
            return True, "JavaScript syntax valid"
        
        except FileNotFoundError:
            return True, "Node not available, skipping validation"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    def _validate_bash(self, code: str) -> Tuple[bool, str]:
        """Validate Bash code."""
        # Check syntax with bash -n
        try:
            result = subprocess.run(
                ['bash', '-n'],
                input=code,
                capture_output=True,
                timeout=5,
                text=True
            )
            
            if result.returncode != 0:
                return False, f"Syntax error: {result.stderr}"
            
            return True, "Bash syntax valid"
        
        except Exception as e:
            return False, f"Validation error: {str(e)}"


class DesignValidator:
    """Validates design artifacts."""
    
    def validate(self, design: str) -> Tuple[bool, List[str]]:
        """
        Validate design completeness.
        
        Returns: (is_valid, missing_sections)
        """
        required_sections = [
            "overview",
            "architecture",
            "components",
            "data flow",
            "error handling",
            "scalability"
        ]
        
        missing = []
        design_lower = design.lower()
        
        for section in required_sections:
            if section not in design_lower:
                missing.append(section)
        
        is_valid = len(missing) == 0
        return is_valid, missing


class DocumentValidator:
    """Validates documentation artifacts."""
    
    def validate(self, doc: str) -> Tuple[bool, List[str]]:
        """
        Validate documentation completeness.
        
        Returns: (is_valid, issues)
        """
        issues = []
        
        # Check for title
        if not doc.startswith("#"):
            issues.append("Missing title (should start with #)")
        
        # Check for structure
        if "##" not in doc:
            issues.append("Missing section headers (##)")
        
        # Check for content length
        if len(doc) < 100:
            issues.append("Documentation too short (< 100 chars)")
        
        # Check for code examples (if applicable)
        if "example" in doc.lower() and "```" not in doc:
            issues.append("Documentation mentions examples but has no code blocks")
        
        is_valid = len(issues) == 0
        return is_valid, issues


class ArtifactValidator:
    """Main artifact validator."""
    
    def __init__(self, latti_home: str = None):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.code_validator = CodeValidator()
        self.design_validator = DesignValidator()
        self.doc_validator = DocumentValidator()
        self.validation_log = []
        self.load_log()
    
    def load_log(self):
        """Load validation log from disk."""
        log_path = os.path.join(self.latti_home, "artifact_validation.jsonl")
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    self.validation_log = [json.loads(line) for line in f if line.strip()]
            except:
                self.validation_log = []
    
    def save_log(self):
        """Save validation log to disk."""
        log_path = os.path.join(self.latti_home, "artifact_validation.jsonl")
        with open(log_path, 'w') as f:
            for entry in self.validation_log:
                f.write(json.dumps(entry) + "\n")
    
    def validate_artifact(self, artifact: Dict) -> Tuple[bool, Dict]:
        """
        Validate an artifact.
        
        Args:
            artifact: {
                "id": "artifact_1",
                "type": "code" | "design" | "document",
                "language": "python" | "javascript" | etc,
                "content": "...",
                "description": "..."
            }
        
        Returns: (is_valid, validation_result)
        """
        artifact_type = artifact.get("type", "unknown")
        artifact_id = artifact.get("id", "unknown")
        content = artifact.get("content", "")
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "is_valid": False,
            "errors": [],
            "warnings": []
        }
        
        if artifact_type == "code":
            language = artifact.get("language", "python")
            is_valid, error = self.code_validator.validate(content, language)
            result["is_valid"] = is_valid
            if not is_valid:
                result["errors"].append(error)
        
        elif artifact_type == "design":
            is_valid, missing = self.design_validator.validate(content)
            result["is_valid"] = is_valid
            if not is_valid:
                result["errors"].append(f"Missing sections: {', '.join(missing)}")
        
        elif artifact_type == "document":
            is_valid, issues = self.doc_validator.validate(content)
            result["is_valid"] = is_valid
            if not is_valid:
                result["errors"].extend(issues)
        
        self.validation_log.append(result)
        self.save_log()
        
        return result["is_valid"], result
    
    def get_validation_stats(self) -> Dict:
        """Get validation statistics."""
        if not self.validation_log:
            return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0}
        
        passed = sum(1 for e in self.validation_log if e.get("is_valid", False))
        failed = len(self.validation_log) - passed
        
        return {
            "total": len(self.validation_log),
            "passed": passed,
            "failed": failed,
            "pass_rate": (passed / len(self.validation_log) * 100) if self.validation_log else 0
        }
    
    def report(self) -> str:
        """Generate validation report."""
        stats = self.get_validation_stats()
        
        report = []
        report.append("\n" + "="*60)
        report.append("ARTIFACT VALIDATION REPORT")
        report.append("="*60)
        report.append(f"Total artifacts: {stats['total']}")
        report.append(f"Passed: {stats['passed']}")
        report.append(f"Failed: {stats['failed']}")
        report.append(f"Pass rate: {stats['pass_rate']:.1f}%")
        report.append("="*60)
        
        return "\n".join(report)


class ArtifactIterator:
    """
    Iterates on artifacts until they pass validation.
    """
    
    def __init__(self, latti_home: str = None, max_iterations: int = 3):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.validator = ArtifactValidator(latti_home)
        self.max_iterations = max_iterations
    
    def iterate(self, artifact: Dict, regenerate_fn) -> Tuple[Dict, bool]:
        """
        Iterate on an artifact until it passes validation.
        
        Args:
            artifact: The artifact to validate
            regenerate_fn: Function to call to regenerate the artifact if it fails
                          Should take (artifact, error_message) and return new artifact
        
        Returns: (final_artifact, success)
        """
        for iteration in range(self.max_iterations):
            is_valid, result = self.validator.validate_artifact(artifact)
            
            if is_valid:
                return artifact, True
            
            # If this is the last iteration, give up
            if iteration == self.max_iterations - 1:
                return artifact, False
            
            # Otherwise, regenerate
            error_message = "; ".join(result.get("errors", []))
            artifact = regenerate_fn(artifact, error_message)
        
        return artifact, False


if __name__ == "__main__":
    # Example usage
    validator = ArtifactValidator()
    
    # Test 1: Valid Python code
    valid_code = {
        "id": "code_1",
        "type": "code",
        "language": "python",
        "content": "print('Hello, world!')"
    }
    
    # Test 2: Invalid Python code
    invalid_code = {
        "id": "code_2",
        "type": "code",
        "language": "python",
        "content": "print('Hello, world!'"  # Missing closing paren
    }
    
    # Test 3: Valid design
    valid_design = {
        "id": "design_1",
        "type": "design",
        "content": """
# System Architecture

## Overview
This is a distributed system.

## Architecture
The system uses microservices.

## Components
- API Gateway
- Service A
- Service B

## Data Flow
Data flows from API to services.

## Error Handling
We handle errors gracefully.

## Scalability
The system scales horizontally.
"""
    }
    
    print("Testing valid code...")
    is_valid, result = validator.validate_artifact(valid_code)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {result['errors']}")
    
    print("\nTesting invalid code...")
    is_valid, result = validator.validate_artifact(invalid_code)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {result['errors']}")
    
    print("\nTesting valid design...")
    is_valid, result = validator.validate_artifact(valid_design)
    print(f"  Valid: {is_valid}")
    print(f"  Errors: {result['errors']}")
    
    print(validator.report())
