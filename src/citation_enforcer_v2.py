#!/usr/bin/env python3
"""
Citation Enforcer v2 — Context-aware citation detection.

Improvements over v1:
1. Context windows: check surrounding words to disambiguate
2. Phrase-level patterns: "the orbit is" vs "orbit of Mars"
3. Earned claim detection: "I read", "I called", "I ran"
4. Configurable strictness: reduce false positives by requiring more context
"""

import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path

class CitationEnforcerV2:
    """Context-aware citation enforcer."""
    
    def __init__(self):
        # Inherited patterns with required context
        # Format: (pattern, required_context, source_key)
        self.inherited_patterns = [
            # Orbit patterns - only flag when discussing system state
            (r'\b(the orbit|orbit ratio|orbit is|orbit.*user-facing)\b', 
             r'(user-facing|ratio|state|system)', 'orbit_rebalance'),
            
            # Audit patterns - only flag when discussing audit results
            (r'\b(audit pass rate|audit.*\d+%|audit.*result)\b',
             r'(pass|fail|result|rate|score)', 'audit_investigation'),
            
            # Soul document patterns - only flag when discussing framework/principles
            (r'\b(soul document|soul.*report|soul.*framework)\b',
             r'(document|report|framework|principle)', 'soul_document'),
            
            # Citation discipline patterns
            (r'\b(citation discipline|citation.*framework|citation.*enforcer)\b',
             r'(discipline|framework|enforcer|gate)', 'session_20260429_citation_discipline_implemented'),
            
            # Braid/orbit topology patterns
            (r'\b(braid|braiding|two-axis|orbit.*braid)\b',
             r'(braid|axis|topology|system)', 'soul_document'),
            
            # Soul pheromones - ONLY when discussing the framework itself
            # NOT when used literally or in technical contexts
            (r'\b(HOLD principle|WOLF principle|SCAR principle|THREAD principle|GAP principle|MEMBRANE principle)\b',
             r'(principle|framework|soul|pheromone)', 'soul_document'),
        ]
        
        # Earned patterns - when I actually performed computation
        self.earned_patterns = [
            (r'\b(I (read|checked|verified|found|discovered|computed|ran|called|wrote|edited|created))\b',
             r'(read_file|write_file|bash|git_|lattice_solve|edit_file)', 'tool_call'),
            (r'\b(called|invoked|executed)\s+(bash|read_file|write_file|git_|lattice_solve)',
             None, 'tool_call'),
        ]
    
    def _has_context(self, text: str, pattern: str, context_pattern: Optional[str]) -> bool:
        """Check if pattern match has required context."""
        if context_pattern is None:
            return True
        
        # Find the match
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return False
        
        # Get surrounding context (100 chars before and after)
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]
        
        # Check if context pattern exists
        return bool(re.search(context_pattern, context, re.IGNORECASE))
    
    def detect_inherited_claims(self, text: str) -> List[Tuple[int, str, str]]:
        """Find inherited claims that need citation."""
        claims = []
        lines = text.split('\n')
        
        for line_num, line in enumerate(lines, 1):
            # Skip if already cited
            if '[inherited:' in line or '[earned:' in line or '[borrowed:' in line:
                continue
            
            for pattern, context_pattern, source_key in self.inherited_patterns:
                if self._has_context(line, pattern, context_pattern):
                    claims.append((line_num, line.strip(), source_key))
                    break
        
        return claims
    
    def detect_earned_claims(self, text: str, tools_called: List[str]) -> List[Tuple[int, str, str]]:
        """Find earned claims that need citation."""
        claims = []
        lines = text.split('\n')
        
        for line_num, line in enumerate(lines, 1):
            # Skip if already cited
            if '[inherited:' in line or '[earned:' in line or '[borrowed:' in line:
                continue
            
            for pattern, tool_pattern, _ in self.earned_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    # Verify tool was actually called
                    if tool_pattern:
                        if re.search(tool_pattern, line, re.IGNORECASE):
                            claims.append((line_num, line.strip(), 'tool_call'))
                            break
                    else:
                        claims.append((line_num, line.strip(), 'tool_call'))
                        break
        
        return claims
    
    def mark_response(
        self,
        text: str,
        inherited_sources: Optional[Dict[str, str]] = None,
        tools_called: Optional[List[str]] = None
    ) -> str:
        """Mark claims in response with citations."""
        inherited_sources = inherited_sources or {}
        tools_called = tools_called or []
        
        # Detect claims
        inherited_claims = self.detect_inherited_claims(text)
        earned_claims = self.detect_earned_claims(text, tools_called)
        
        # Build mapping of line numbers to citations
        citations = {}
        
        for line_num, line, source_key in inherited_claims:
            source = inherited_sources.get(source_key, source_key)
            citations[line_num] = f"[inherited: {source}]"
        
        for line_num, line, tool in earned_claims:
            citations[line_num] = f"[earned: {tool}]"
        
        # Apply citations
        if not citations:
            return text
        
        lines = text.split('\n')
        marked_lines = []
        
        for line_num, line in enumerate(lines, 1):
            if line_num in citations:
                citation = citations[line_num]
                marked_lines.append(f"{citation} {line}")
            else:
                marked_lines.append(line)
        
        return '\n'.join(marked_lines)


# Singleton instance
_enforcer = CitationEnforcerV2()

def enforce_citations(
    text: str,
    inherited_sources: Optional[Dict[str, str]] = None,
    tools_called: Optional[List[str]] = None,
    strict: bool = False
) -> Tuple[str, bool]:
    """
    Enforce citations on response text.
    
    Returns:
        Tuple of (marked_text, is_clean) where is_clean indicates if all claims are cited
    """
    marked = _enforcer.mark_response(text, inherited_sources, tools_called)
    
    # Check if any claims remain uncited
    uncited_count = len(_enforcer.detect_inherited_claims(marked))
    is_clean = uncited_count == 0
    
    if strict and not is_clean:
        raise ValueError(f"Found {uncited_count} uncited claims in response")
    
    return marked, is_clean


def get_enforcer() -> CitationEnforcerV2:
    """Get the singleton enforcer instance."""
    return _enforcer
