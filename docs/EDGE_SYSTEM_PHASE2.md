# LATTI EDGE SYSTEM PHASE 2
## Artifact Validation & Regeneration

**Date:** 2026-05-03  
**Status:** Phase 2 Complete — Validator + Regenerator Built  
**Bottleneck:** Artifact Quality (score: 25/100)

## What Was Built

### 1. Artifact Validator (`artifact_validator.py`)
Validates artifacts before they reach the user:
- **Code validation:** Syntax check + runtime test
- **Design validation:** Completeness check (all required sections present)
- **Document validation:** Structure check (title, sections, examples)

Supports: Python, JavaScript, Bash, and more

### 2. Artifact Regenerator (`artifact_regenerator.py`)
Regenerates artifacts that fail validation:
- Extracts error message
- Creates regeneration prompt
- Calls LLM to fix it
- Validates again
- Repeats until passing or max attempts (default: 3)

### 3. Artifact Quality Gate (`ArtifactQualityGate`)
Ensures all artifacts are valid before reaching the user:
- Validates on first pass
- If invalid, regenerates (if LLM function provided)
- Returns only valid artifacts

## How It Works

```
Artifact Generated
    ↓
[Artifact Validator]
    ├─ Valid? → Return to user
    └─ Invalid? → Extract error
        ↓
[Artifact Regenerator]
    ├─ Call LLM with error context
    ├─ Validate regenerated artifact
    ├─ Passed? → Return to user
    └─ Failed? → Retry (max 3 times)
        ↓
[Final Artifact]
    ├─ Valid → Return to user
    └─ Invalid → Return with errors
```

## Validation Rules

### Code
- **Syntax:** Must compile without errors
- **Runtime:** Must execute without errors (5s timeout)
- **Languages:** Python, JavaScript, Bash (extensible)

### Design
- **Required sections:** overview, architecture, components, data flow, error handling, scalability
- **Completeness:** All sections must be present
- **Clarity:** Must be implementable

### Documents
- **Structure:** Must have title (#) and sections (##)
- **Length:** Minimum 100 characters
- **Examples:** If mentioned, must include code blocks

## Integration Points

### 1. In Agent Runtime
```python
from artifact_validator import ArtifactValidator
from artifact_regenerator import ArtifactRegenerator

validator = ArtifactValidator()
regenerator = ArtifactRegenerator()

# After generating artifact
is_valid, result = validator.validate_artifact(artifact)
if not is_valid:
    artifact = regenerator.iterate_until_valid(artifact, llm_call_fn)
```

### 2. In LLM Response Handler
```python
from artifact_regenerator import ArtifactQualityGate

gate = ArtifactQualityGate()

# Process artifact through quality gate
artifact = gate.process_artifact(artifact, llm_call_fn)

# Return to user
return artifact
```

## Metrics to Track

- **Validation Pass Rate:** Target 90%+ (from 67%)
- **Regeneration Success Rate:** Target 85%+ (from 0%)
- **Avg Iterations:** Target < 1.5 (from 0)
- **Artifact Quality Score:** Target 75+ (from 25)

## Files Created

- `src/artifact_validator.py` — Validation logic
- `src/artifact_regenerator.py` — Regeneration logic
- `docs/EDGE_SYSTEM_PHASE2.md` — This document

## Testing

All modules tested and working:
```bash
python3 ~/.latti/artifact_validator.py      # Validation tests
python3 ~/.latti/artifact_regenerator.py    # Regeneration tests
```

Results:
- Valid code: ✓ Passes
- Invalid code: ✓ Caught
- Valid design: ✓ Passes
- Regeneration: ✓ Works

## Next Steps

### Phase 3: Routing Intelligence
Once artifact quality improves:
1. Build decision tree from past successes
2. Learn which model/tool works best for each task type
3. Auto-adjust complexity thresholds
4. Optimize cost vs quality tradeoff

### Phase 4: End-to-End Integration
1. Wire validator into agent runtime
2. Wire regenerator into LLM response handler
3. Monitor all three dimensions (reasoning, artifacts, routing)
4. Adjust thresholds based on real-world performance

## Integration Checklist

- [ ] Import ArtifactValidator in agent runtime
- [ ] Import ArtifactRegenerator in LLM response handler
- [ ] Call validator.validate_artifact() after generation
- [ ] Call regenerator.iterate_until_valid() if invalid
- [ ] Monitor validation pass rate
- [ ] Monitor regeneration success rate
- [ ] Adjust validation rules based on results
- [ ] Move to Phase 3 when artifact quality > 50

## Performance Targets

| Metric | Current | Target | Phase |
|--------|---------|--------|-------|
| Reasoning Depth | 0/100 | 75/100 | 1 |
| Artifact Quality | 25/100 | 75/100 | 2 |
| Routing Accuracy | 25/100 | 75/100 | 3 |
| **Overall System** | **16/100** | **75/100** | **4** |

---

**Built by:** Latti  
**For:** Manolito Nora  
**Mission:** Get Latti to the edge — better than frontier models on reasoning, artifacts, and routing.
