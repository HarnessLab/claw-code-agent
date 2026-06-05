# Edge System Integration V2 - API Reference

## Table of Contents

1. [EdgeSystemIntegrationV2](#edgesystemintegrationv2)
2. [EdgeSystemHookV2](#edgesystemhookv2)
3. [Data Structures](#data-structures)
4. [Error Handling](#error-handling)

---

## EdgeSystemIntegrationV2

Main integration class for Phase 5 optimization.

### Constructor

```python
EdgeSystemIntegrationV2(
    models: List[str] = None,
    latti_home: str = None
)
```

**Parameters:**
- `models` (List[str], optional): List of model names. Defaults to `["gpt-3.5", "gpt-4", "claude"]`
- `latti_home` (str, optional): Path to LATTI home directory. Defaults to `~/.latti`

**Returns:** EdgeSystemIntegrationV2 instance

**Example:**
```python
# Default models
integration = EdgeSystemIntegrationV2()

# Custom models
integration = EdgeSystemIntegrationV2(
    models=["model-a", "model-b", "model-c"],
    latti_home="/custom/path/.latti"
)
```

---

### process_task

Routes a task to the most appropriate model based on complexity.

```python
def process_task(task: Dict[str, Any]) -> Dict[str, Any]
```

**Parameters:**
- `task` (Dict[str, Any]): Task object with at least `id` and `description` fields

**Returns:** Dict with routing decision and metadata

**Return Structure:**
```python
{
    "model": str,  # Selected model name
    "routing_metadata": {
        "complexity_score": float,  # 0-10 complexity score
        "recommended_model": str,   # Recommended model
        "confidence": float         # 0-1 confidence score
    }
}
```

**Example:**
```python
task = {
    "id": "task_1",
    "description": "Design a distributed cache system",
    "type": "architecture"
}

result = integration.process_task(task)
print(result["model"])  # "gpt-4"
print(result["routing_metadata"]["complexity_score"])  # 8.5
```

---

### record_execution

Records the outcome of a task execution.

```python
def record_execution(
    task_id: str,
    model: str,
    success: bool,
    quality: int = 0,
    cost: int = 0,
    error_type: str = None,
    error_message: str = None,
    regenerations: int = 0
) -> None
```

**Parameters:**
- `task_id` (str): Unique task identifier
- `model` (str): Model used for execution
- `success` (bool): Whether execution was successful
- `quality` (int, optional): Quality score (0-100). Defaults to 0
- `cost` (int, optional): Execution cost in tokens. Defaults to 0
- `error_type` (str, optional): Type of error if failed. Defaults to None
- `error_message` (str, optional): Error message if failed. Defaults to None
- `regenerations` (int, optional): Number of regenerations. Defaults to 0

**Returns:** None

**Example:**
```python
# Successful execution
integration.record_execution(
    task_id="task_1",
    model="gpt-4",
    success=True,
    quality=85,
    cost=2000
)

# Failed execution
integration.record_execution(
    task_id="task_2",
    model="gpt-3.5",
    success=False,
    quality=0,
    cost=1000,
    error_type="timeout",
    error_message="Task exceeded time limit"
)
```

---

### optimize

Runs optimization to compute Pareto frontier and recommendations.

```python
def optimize() -> Dict[str, Any]
```

**Parameters:** None

**Returns:** Dict with optimization results

**Return Structure:**
```python
{
    "timestamp": str,  # ISO format timestamp
    "optimizer_frontier": [
        {
            "model": str,           # Model name
            "cost": float,          # Average cost
            "quality": float,       # Average quality
            "efficiency": float     # Quality/cost ratio
        },
        ...
    ],
    "recommendations": [
        {
            "scenario": str,        # "cost_sensitive", "quality_focused", "balanced"
            "model": str,           # Recommended model
            "expected_quality": float,
            "expected_cost": float
        },
        ...
    ]
}
```

**Example:**
```python
opt_results = integration.optimize()

print("Pareto Frontier:")
for point in opt_results["optimizer_frontier"]:
    print(f"  {point['model']}: cost={point['cost']}, quality={point['quality']}")

print("\nRecommendations:")
for rec in opt_results["recommendations"]:
    print(f"  {rec['scenario']}: {rec['model']}")
```

---

### get_stats

Returns comprehensive statistics about model performance.

```python
def get_stats() -> Dict[str, Any]
```

**Parameters:** None

**Returns:** Dict with bandit and analyzer statistics

**Return Structure:**
```python
{
    "bandit_stats": {
        "model_name": {
            "success_rate": float,      # 0-1
            "avg_quality": float,       # 0-100
            "avg_cost": float,          # Average tokens
            "total_tasks": int
        },
        ...
    },
    "analyzer_stats": {
        "total_failures": int,
        "most_common_errors": [
            (error_type, count),
            ...
        ],
        "failure_rate": float           # 0-1
    }
}
```

**Example:**
```python
stats = integration.get_stats()

print("Model Performance:")
for model, metrics in stats["bandit_stats"].items():
    print(f"  {model}:")
    print(f"    Success Rate: {metrics['success_rate']:.1%}")
    print(f"    Avg Quality: {metrics['avg_quality']:.1f}")
    print(f"    Avg Cost: {metrics['avg_cost']:.0f} tokens")

print("\nFailure Analysis:")
print(f"  Total Failures: {stats['analyzer_stats']['total_failures']}")
print(f"  Failure Rate: {stats['analyzer_stats']['failure_rate']:.1%}")
```

---

### get_recovery_strategy

Returns recovery strategy for a failed task.

```python
def get_recovery_strategy(task_id: str) -> Tuple[str, str]
```

**Parameters:**
- `task_id` (str): ID of the failed task

**Returns:** Tuple of (strategy_type, strategy_description)

**Strategy Types:**
- `"retry_with_upgrade"`: Retry with a more capable model
- `"retry_with_downgrade"`: Retry with a simpler model
- `"retry_with_same"`: Retry with the same model
- `"manual_intervention"`: Requires manual review
- `"skip"`: Skip this task

**Example:**
```python
strategy_type, strategy_desc = integration.get_recovery_strategy("task_1")

if strategy_type == "retry_with_upgrade":
    print(f"Retry with a more capable model: {strategy_desc}")
elif strategy_type == "manual_intervention":
    print(f"Manual review needed: {strategy_desc}")
```

---

### report

Generates a human-readable report of system performance.

```python
def report() -> str
```

**Parameters:** None

**Returns:** Formatted report string

**Example:**
```python
report = integration.report()
print(report)

# Output:
# ╔════════════════════════════════════════════════════════════╗
# ║         Edge System Integration V2 - Performance Report     ║
# ╚════════════════════════════════════════════════════════════╝
# 
# Model Performance:
# ─────────────────────────────────────────────────────────────
# gpt-3.5:
#   Success Rate: 95.0%
#   Avg Quality: 78.0
#   Avg Cost: 1200 tokens
#   Total Tasks: 20
# ...
```

---

## EdgeSystemHookV2

Hook interface for integration with agent runtime.

### Constructor

```python
EdgeSystemHookV2()
```

**Returns:** EdgeSystemHookV2 instance

**Example:**
```python
hook = EdgeSystemHookV2()
```

---

### process_task

Routes a task (same as EdgeSystemIntegrationV2.process_task).

```python
def process_task(task: Dict[str, Any]) -> Dict[str, Any]
```

See [EdgeSystemIntegrationV2.process_task](#process_task)

---

### record_result

Records execution result (same as EdgeSystemIntegrationV2.record_execution).

```python
def record_result(
    task_id: str,
    model: str,
    success: bool,
    quality: int = 0,
    cost: int = 0,
    error_type: str = None,
    error_message: str = None,
    regenerations: int = 0
) -> None
```

See [EdgeSystemIntegrationV2.record_execution](#record_execution)

---

### get_stats

Returns statistics (same as EdgeSystemIntegrationV2.get_stats).

```python
def get_stats() -> Dict[str, Any]
```

See [EdgeSystemIntegrationV2.get_stats](#get_stats)

---

### optimize

Runs optimization (same as EdgeSystemIntegrationV2.optimize).

```python
def optimize() -> Dict[str, Any]
```

See [EdgeSystemIntegrationV2.optimize](#optimize)

---

### report

Generates report (same as EdgeSystemIntegrationV2.report).

```python
def report() -> str
```

See [EdgeSystemIntegrationV2.report](#report)

---

## Global Hook Functions

### get_edge_hook_v2

Returns the global singleton hook instance.

```python
def get_edge_hook_v2() -> EdgeSystemHookV2
```

**Returns:** Global EdgeSystemHookV2 instance

**Example:**
```python
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()
result = hook.process_task(task)
```

---

## Data Structures

### Task Object

```python
{
    "id": str,              # Unique task identifier
    "description": str,     # Task description
    "type": str,           # Task type (optional)
    "priority": int,       # Priority level (optional)
    "context": dict        # Additional context (optional)
}
```

### Execution Record

```python
{
    "task_id": str,
    "model": str,
    "timestamp": str,      # ISO format
    "success": bool,
    "quality": int,        # 0-100
    "cost": int,           # Tokens
    "error_type": str,     # None if successful
    "error_message": str,  # None if successful
    "regenerations": int
}
```

### Routing Decision

```python
{
    "model": str,
    "routing_metadata": {
        "complexity_score": float,  # 0-10
        "recommended_model": str,
        "confidence": float         # 0-1
    }
}
```

### Optimization Result

```python
{
    "timestamp": str,
    "optimizer_frontier": [
        {
            "model": str,
            "cost": float,
            "quality": float,
            "efficiency": float
        }
    ],
    "recommendations": [
        {
            "scenario": str,
            "model": str,
            "expected_quality": float,
            "expected_cost": float
        }
    ]
}
```

### Statistics

```python
{
    "bandit_stats": {
        "model_name": {
            "success_rate": float,
            "avg_quality": float,
            "avg_cost": float,
            "total_tasks": int
        }
    },
    "analyzer_stats": {
        "total_failures": int,
        "most_common_errors": [(str, int)],
        "failure_rate": float
    }
}
```

---

## Error Handling

### Common Error Types

```python
# Timeout
integration.record_execution(
    task_id="task_1",
    model="gpt-4",
    success=False,
    error_type="timeout",
    error_message="Task exceeded 30s limit"
)

# Memory Error
integration.record_execution(
    task_id="task_2",
    model="gpt-4",
    success=False,
    error_type="memory_error",
    error_message="Out of memory"
)

# Rate Limit
integration.record_execution(
    task_id="task_3",
    model="gpt-3.5",
    success=False,
    error_type="rate_limit",
    error_message="Rate limit exceeded"
)

# Invalid Input
integration.record_execution(
    task_id="task_4",
    model="gpt-4",
    success=False,
    error_type="invalid_input",
    error_message="Invalid task format"
)
```

### Recovery Strategies

```python
strategy_type, description = integration.get_recovery_strategy(task_id)

if strategy_type == "retry_with_upgrade":
    # Use a more capable model
    pass
elif strategy_type == "retry_with_downgrade":
    # Use a simpler model
    pass
elif strategy_type == "retry_with_same":
    # Retry with same model
    pass
elif strategy_type == "manual_intervention":
    # Requires human review
    pass
elif strategy_type == "skip":
    # Skip this task
    pass
```

---

## Complete Example

```python
from edge_system_integration_v2 import EdgeSystemIntegrationV2

# Initialize
integration = EdgeSystemIntegrationV2()

# Process multiple tasks
tasks = [
    {"id": "t1", "description": "Design a cache system", "type": "architecture"},
    {"id": "t2", "description": "Write a REST API", "type": "code"},
    {"id": "t3", "description": "Debug a memory leak", "type": "debugging"}
]

for task in tasks:
    # Route task
    routed = integration.process_task(task)
    model = routed["model"]
    
    # Execute (simulated)
    try:
        result = execute_task(model, task)
        success = True
        quality = result["quality"]
        cost = result["cost"]
        error_type = None
        error_message = None
    except Exception as e:
        success = False
        quality = 0
        cost = 0
        error_type = type(e).__name__
        error_message = str(e)
    
    # Record result
    integration.record_execution(
        task_id=task["id"],
        model=model,
        success=success,
        quality=quality,
        cost=cost,
        error_type=error_type,
        error_message=error_message
    )

# Analyze results
stats = integration.get_stats()
opt_results = integration.optimize()
report = integration.report()

print(report)
```

---

## Version

- **Version:** 2.0
- **Phase:** 5 (Optimization)
- **Last Updated:** 2024-01-15
