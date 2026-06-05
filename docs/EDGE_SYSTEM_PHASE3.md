# LATTI EDGE SYSTEM PHASE 3

## Routing Intelligence

**Date:** 2026-05-03  
**Status:** Phase 3 Complete — Routing Decision Tree + Complexity Analyzer + Optimizer Built  
**Bottleneck:** Model Selection (need to learn which model works best for each task)

---

## What Was Built

### 1. Routing Decision Tree (`routing_decision_tree.py`)

Learns which model/tool works best for each task type.

**Structure:**
```
task_type (code, design, doc, analysis)
  ├─ complexity_level (simple, medium, complex)
  │   ├─ model (gpt-3.5, gpt-4, claude, etc.)
  │   ├─ tool (code_generator, design_generator, etc.)
  │   ├─ cost_limit (tokens)
  │   ├─ quality_threshold (0-100)
  │   └─ success_rate (0-1)
  └─ fallback_model
```

**Key Methods:**
- `route(task_type, complexity)` → RouteDecision
- `record_outcome(task_type, complexity, model, success, cost, quality)`
- `optimize()` → adjusts thresholds based on outcomes
- `stats()` → returns routing statistics

**Example:**
```python
tree = RoutingDecisionTree()
route = tree.route("code", 0.7)  # complexity 0.7 = medium-complex
# Returns: RouteDecision(model="gpt-4", tool="code_generator", cost_limit=5000, ...)

tree.record_outcome("code", 0.7, "gpt-4", success=True, cost=3000, quality=92)
tree.optimize()  # Adjusts thresholds
```

### 2. Complexity Analyzer (`complexity_analyzer.py`)

Measures task complexity to predict which model tier is needed.

**Factors (weighted):**
- Token count (25%) — input + expected output size
- Nesting depth (20%) — function calls, loops, conditionals
- Dependencies (20%) — external libraries, APIs, databases
- Ambiguity (20%) — unclear requirements, edge cases
- Scope (15%) — lines of code, number of components

**Output:** Complexity score (0-1)
- 0.0-0.33: simple (gpt-3.5 sufficient)
- 0.33-0.67: medium (gpt-4 recommended)
- 0.67-1.0: complex (gpt-4 required, may need iteration)

**Example:**
```python
analyzer = ComplexityAnalyzer()
complexity = analyzer.analyze("Write a REST API endpoint...", task_type="code")
# Returns: 0.65 (medium-complex)

analysis = analyzer.detailed_analysis(task_description, "code")
# Returns: {
#   "complexity": 0.65,
#   "level": "medium",
#   "scores": {"token_count": 0.15, "nesting_depth": 0.20, ...},
#   "weights": {...}
# }
```

### 3. Routing Optimizer (`routing_optimizer.py`)

Adjusts routing thresholds based on real-world performance.

**Monitors:**
- Success rate per route (model + task type + complexity)
- Cost per route (tokens used)
- Quality per route (artifact quality score)
- Failure modes (what goes wrong and why)

**Optimizes:**
- Cost limits (increase if failing, decrease if succeeding)
- Quality thresholds (adjust based on actual quality)
- Model selection (switch models if one consistently outperforms)
- Complexity thresholds (adjust simple/medium/complex boundaries)

**Optimization Rules:**
1. **Low success rate (<60%)** → increase cost limit by 20%
2. **High success rate (>85%) + high quality (>80)** → decrease cost limit by 10%
3. **Low quality (<70)** → increase quality threshold
4. **Model comparison** → recommend switching if one outperforms by >20% success rate + >10 quality points

**Example:**
```python
optimizer = RoutingOptimizer()
optimizer.record_outcome("code", 0.5, "gpt-4", success=True, cost=3000, quality=92)
optimizer.record_outcome("code", 0.5, "gpt-4", success=True, cost=3100, quality=95)
# ... more outcomes ...

changes = optimizer.optimize()
# Returns: {"code/medium/gpt-4": {"reason": "high success + quality", "action": "decrease cost limit by 10%"}}

recommendations = optimizer.recommend_model_switch()
# Returns: {"code/medium": {"current_model": "gpt-3.5", "recommended_model": "gpt-4", ...}}

stats = optimizer.stats()
# Returns: {"overall_success_rate": 0.85, "overall_avg_quality": 88, "routes": {...}}
```

---

## Files Created

- `src/routing_decision_tree.py` (10.8 KB)
- `src/complexity_analyzer.py` (7.4 KB)
- `src/routing_optimizer.py` (10.5 KB)
- `docs/EDGE_SYSTEM_PHASE3.md` (this file)

---

## How It Works

### 1. Task Arrives

```
User: "Build a distributed cache system..."
```

### 2. Complexity Analysis

```python
analyzer = ComplexityAnalyzer()
complexity = analyzer.analyze(task_description, "code")
# complexity = 0.75 (complex)
```

### 3. Routing Decision

```python
tree = RoutingDecisionTree()
route = tree.route("code", 0.75)
# route = RouteDecision(model="gpt-4", cost_limit=10000, quality_threshold=85)
```

### 4. Execution

```
LLM generates artifact using gpt-4
Artifact validator checks quality
If quality >= 85: success
If quality < 85: regenerate or escalate
```

### 5. Outcome Recording

```python
tree.record_outcome("code", 0.75, "gpt-4", success=True, cost=8000, quality=92)
```

### 6. Optimization (periodic)

```python
optimizer = RoutingOptimizer()
changes = optimizer.optimize()
# Adjusts cost limits, quality thresholds, model selection
```

---

## Metrics to Track

### Per-Route Metrics
- **Success Rate:** % of tasks that pass validation
- **Avg Cost:** Average tokens used
- **Avg Quality:** Average artifact quality score
- **Outcomes:** Number of tasks routed

### Overall Metrics
- **Overall Success Rate:** % of all tasks passing validation
- **Overall Avg Quality:** Average quality across all tasks
- **Cost Efficiency:** Cost per quality point
- **Model Distribution:** % of tasks using each model

### Target Metrics (Phase 3)
- Overall success rate: **67% → 80%**
- Overall avg quality: **25 → 60**
- Cost efficiency: **TBD → optimize**

---

## Testing Results

### Routing Decision Tree
✓ Routes simple tasks to gpt-3.5 (cost_limit=2000)
✓ Routes complex tasks to gpt-4 (cost_limit=10000)
✓ Tracks success rates and updates them
✓ Saves/loads tree from disk

### Complexity Analyzer
✓ Scores simple tasks as 0.0-0.33
✓ Scores medium tasks as 0.33-0.67
✓ Scores complex tasks as 0.67-1.0
✓ Provides detailed breakdown of factors

### Routing Optimizer
✓ Records outcomes and updates metrics
✓ Recommends cost limit adjustments
✓ Recommends model switches
✓ Provides comprehensive statistics

---

## Integration Checklist

- [ ] Import RoutingDecisionTree in agent runtime
- [ ] Import ComplexityAnalyzer in task handler
- [ ] Import RoutingOptimizer in outcome handler
- [ ] Call analyzer.analyze() on incoming task
- [ ] Call tree.route() to get routing decision
- [ ] Call optimizer.record_outcome() after execution
- [ ] Call optimizer.optimize() periodically (e.g., every 100 tasks)
- [ ] Monitor metrics and adjust thresholds
- [ ] Move to Phase 4 when overall success rate > 75%

---

## Next Steps

### Phase 4: End-to-End Integration
- Wire validator into agent runtime
- Wire regenerator into LLM response handler
- Wire routing intelligence into task dispatcher
- Monitor all three dimensions (validation, regeneration, routing)
- Adjust thresholds based on real-world performance
- Build dashboard to visualize metrics

### Phase 5: Advanced Optimization
- Multi-armed bandit for model selection
- Bayesian optimization for cost/quality tradeoff
- Failure mode analysis and recovery
- Cost prediction and budgeting
- Quality prediction and escalation

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    INCOMING TASK                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │  COMPLEXITY ANALYZER           │
        │  - Token count                 │
        │  - Nesting depth               │
        │  - Dependencies                │
        │  - Ambiguity                   │
        │  - Scope                       │
        └────────────┬───────────────────┘
                     │
                     ▼ (complexity: 0-1)
        ┌────────────────────────────────┐
        │  ROUTING DECISION TREE         │
        │  - Task type → model           │
        │  - Complexity → cost limit     │
        │  - Success rate tracking       │
        └────────────┬───────────────────┘
                     │
                     ▼ (route decision)
        ┌────────────────────────────────┐
        │  LLM EXECUTION                 │
        │  - Generate artifact           │
        │  - Validate quality            │
        │  - Regenerate if needed        │
        └────────────┬───────────────────┘
                     │
                     ▼ (outcome)
        ┌────────────────────────────────┐
        │  ROUTING OPTIMIZER             │
        │  - Record outcome              │
        │  - Update metrics              │
        │  - Recommend adjustments       │
        └────────────┬───────────────────┘
                     │
                     ▼
        ┌────────────────────────────────┐
        │  PERIODIC OPTIMIZATION         │
        │  - Adjust cost limits          │
        │  - Adjust quality thresholds   │
        │  - Recommend model switches    │
        └────────────────────────────────┘
```

---

## Code Examples

### Example 1: Simple Integration

```python
from routing_decision_tree import RoutingDecisionTree
from complexity_analyzer import ComplexityAnalyzer
from routing_optimizer import RoutingOptimizer

# Initialize
tree = RoutingDecisionTree()
analyzer = ComplexityAnalyzer()
optimizer = RoutingOptimizer()

# Process task
task_description = "Build a REST API endpoint..."
complexity = analyzer.analyze(task_description, "code")
route = tree.route("code", complexity)

print(f"Route: {route.model} (cost_limit={route.cost_limit})")

# Execute (pseudo-code)
artifact = llm.generate(task_description, model=route.model)
quality = validator.validate(artifact)

# Record outcome
optimizer.record_outcome(
    "code", complexity, route.model,
    success=(quality >= route.quality_threshold),
    cost=artifact.tokens_used,
    quality=quality
)
```

### Example 2: Periodic Optimization

```python
# Every 100 tasks
if task_count % 100 == 0:
    changes = optimizer.optimize()
    recommendations = optimizer.recommend_model_switch()
    stats = optimizer.stats()
    
    print(f"Overall success rate: {stats['overall_success_rate']}")
    print(f"Overall avg quality: {stats['overall_avg_quality']}")
    print(f"Recommended changes: {changes}")
    print(f"Model switches: {recommendations}")
```

### Example 3: Detailed Analysis

```python
analysis = analyzer.detailed_analysis(task_description, "code")
print(f"Complexity: {analysis['complexity']}")
print(f"Level: {analysis['level']}")
print(f"Scores: {analysis['scores']}")
print(f"Weights: {analysis['weights']}")

# Scores breakdown:
# - token_count: 0.15 (15% of complexity)
# - nesting_depth: 0.20 (20% of complexity)
# - dependencies: 0.30 (30% of complexity)
# - ambiguity: 0.00 (0% of complexity)
# - scope: 0.02 (2% of complexity)
# Total: 0.67 (medium-complex)
```

---

## Performance Targets

| Metric | Phase 2 | Phase 3 | Phase 4 |
|--------|---------|---------|---------|
| Validation Pass Rate | 67% | 75% | 85% |
| Regeneration Success | 0% | 50% | 85% |
| Routing Accuracy | N/A | 70% | 90% |
| Overall Quality | 25/100 | 50/100 | 75/100 |
| Cost Efficiency | N/A | TBD | Optimized |

---

## Commit

```
commit: 53fedbe (Phase 2)
message: build: edge system phase 2 — artifact validation & regeneration

commit: [Phase 3 - pending]
message: build: edge system phase 3 — routing intelligence

Files:
- src/routing_decision_tree.py
- src/complexity_analyzer.py
- src/routing_optimizer.py
- docs/EDGE_SYSTEM_PHASE3.md
```
