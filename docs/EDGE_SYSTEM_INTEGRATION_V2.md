# Edge System Integration V2 (Phase 5)

## Overview

**EdgeSystemIntegrationV2** is the Phase 5 optimization layer that integrates Phase 4 edge system components (router, upgrader, diagnostic) with Phase 5 optimization components (bandit, optimizer, analyzer).

This system enables:
- **Intelligent task routing** based on complexity and model capabilities
- **Multi-armed bandit learning** to optimize model selection
- **Pareto frontier optimization** for cost/quality tradeoffs
- **Failure mode analysis** and recovery strategies
- **State persistence** across sessions

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│         EdgeSystemIntegrationV2 (Phase 5)                   │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Phase 4 Edge System Components                       │   │
│  ├──────────────────────────────────────────────────────┤   │
│  │ • Router: Task routing & complexity scoring          │   │
│  │ • Upgrader: Model capability management              │   │
│  │ • Diagnostic: System health monitoring               │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Phase 5 Optimization Components                      │   │
│  ├──────────────────────────────────────────────────────┤   │
│  │ • Bandit: Multi-armed bandit learning                │   │
│  │ • Optimizer: Pareto frontier computation             │   │
│  │ • Analyzer: Failure mode analysis                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Persistent State Management                          │   │
│  ├──────────────────────────────────────────────────────┤   │
│  │ • Task results history                               │   │
│  │ • Model performance metrics                          │   │
│  │ • Optimization results                               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. EdgeSystemIntegrationV2

Main integration class that orchestrates all components.

```python
from edge_system_integration_v2 import EdgeSystemIntegrationV2

# Initialize with default models
integration = EdgeSystemIntegrationV2()

# Or with custom models
integration = EdgeSystemIntegrationV2(
    models=["gpt-3.5", "gpt-4", "claude", "custom-model"]
)
```

#### Key Methods

**process_task(task: dict) → dict**
Routes a task to the most appropriate model based on complexity.

```python
task = {
    "id": "task_1",
    "description": "Design a distributed cache system",
    "type": "architecture"
}

result = integration.process_task(task)
# Returns:
# {
#     "model": "gpt-4",
#     "routing_metadata": {
#         "complexity_score": 8.5,
#         "recommended_model": "gpt-4",
#         "confidence": 0.92
#     }
# }
```

**record_execution(...) → None**
Records the outcome of a task execution.

```python
integration.record_execution(
    task_id="task_1",
    model="gpt-4",
    success=True,
    quality=85,
    cost=2000,
    error_type=None,
    error_message=None,
    regenerations=0
)
```

**optimize() → dict**
Runs optimization to compute Pareto frontier and recommendations.

```python
opt_results = integration.optimize()
# Returns:
# {
#     "timestamp": "2024-01-15T10:30:00Z",
#     "optimizer_frontier": [
#         {
#             "model": "gpt-3.5",
#             "cost": 1000,
#             "quality": 75,
#             "efficiency": 0.075
#         },
#         ...
#     ],
#     "recommendations": [
#         {
#             "scenario": "cost_sensitive",
#             "model": "gpt-3.5",
#             "expected_quality": 75,
#             "expected_cost": 1000
#         },
#         ...
#     ]
# }
```

**get_stats() → dict**
Returns comprehensive statistics about model performance.

```python
stats = integration.get_stats()
# Returns:
# {
#     "bandit_stats": {
#         "gpt-3.5": {
#             "success_rate": 0.95,
#             "avg_quality": 78,
#             "avg_cost": 1200,
#             "total_tasks": 20
#         },
#         ...
#     },
#     "analyzer_stats": {
#         "total_failures": 5,
#         "most_common_errors": [
#             ("timeout", 3),
#             ("memory_error", 2)
#         ],
#         "failure_rate": 0.05
#     }
# }
```

**get_recovery_strategy(task_id: str) → tuple**
Returns recovery strategy for a failed task.

```python
strategy_type, strategy_desc = integration.get_recovery_strategy("task_1")
# Returns:
# ("retry_with_upgrade", "Retry with gpt-4 instead of gpt-3.5")
```

**report() → str**
Generates a human-readable report of system performance.

```python
report = integration.report()
print(report)
```

### 2. EdgeSystemHookV2

Hook interface for integration with agent runtime.

```python
from edge_system_integration_v2 import EdgeSystemHookV2

hook = EdgeSystemHookV2()

# Process task
result = hook.process_task(task)

# Record result
hook.record_result(
    task_id="task_1",
    model="gpt-4",
    success=True,
    quality=85,
    cost=2000
)

# Get stats
stats = hook.get_stats()

# Run optimization
opt_results = hook.optimize()

# Generate report
report = hook.report()
```

### 3. Global Hook Instance

Access the global hook instance:

```python
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()  # Singleton instance
```

## Workflow Example

### Complete Task Processing Workflow

```python
from edge_system_integration_v2 import EdgeSystemIntegrationV2

# Initialize
integration = EdgeSystemIntegrationV2()

# Define tasks
tasks = [
    {
        "id": "task_1",
        "description": "Design a distributed cache system",
        "type": "architecture"
    },
    {
        "id": "task_2",
        "description": "Write a REST API endpoint",
        "type": "code"
    }
]

# Process each task
for task in tasks:
    # 1. Route task to appropriate model
    routed = integration.process_task(task)
    selected_model = routed["model"]
    
    # 2. Execute task with selected model
    # (This would be done by the agent runtime)
    result = execute_with_model(selected_model, task)
    
    # 3. Record execution outcome
    integration.record_execution(
        task_id=task["id"],
        model=selected_model,
        success=result["success"],
        quality=result["quality"],
        cost=result["cost"],
        error_type=result.get("error_type"),
        error_message=result.get("error_message")
    )

# 4. Run optimization
opt_results = integration.optimize()

# 5. Get statistics
stats = integration.get_stats()

# 6. Generate report
report = integration.report()
print(report)
```

## Integration with Agent Runtime

### Hook Integration Pattern

```python
from edge_system_integration_v2 import get_edge_hook_v2

class AgentRuntime:
    def __init__(self):
        self.hook = get_edge_hook_v2()
    
    def process_task(self, task):
        # Route task using hook
        routed = self.hook.process_task(task)
        model = routed["model"]
        
        # Execute task
        try:
            result = self.execute(model, task)
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
        self.hook.record_result(
            task_id=task["id"],
            model=model,
            success=success,
            quality=quality,
            cost=cost
        )
        
        return result
    
    def get_optimization_report(self):
        # Get stats
        stats = self.hook.get_stats()
        
        # Run optimization
        opt_results = self.hook.optimize()
        
        # Generate report
        report = self.hook.report()
        
        return {
            "stats": stats,
            "optimization": opt_results,
            "report": report
        }
```

## State Persistence

The system automatically persists state to `~/.latti/edge_system_v2/`:

```
~/.latti/edge_system_v2/
├── task_results.json      # All task execution records
├── optimization_results.json  # Optimization history
└── state.json             # Current system state
```

State is automatically loaded on initialization:

```python
# First session
integration1 = EdgeSystemIntegrationV2()
integration1.record_execution(...)

# Second session - state is automatically loaded
integration2 = EdgeSystemIntegrationV2()
# integration2 has all previous task results
```

## Performance Metrics

### Bandit Statistics

For each model, the system tracks:
- **success_rate**: Percentage of successful executions
- **avg_quality**: Average quality score
- **avg_cost**: Average execution cost
- **total_tasks**: Total number of tasks executed

### Optimizer Frontier

The Pareto frontier shows optimal cost/quality tradeoffs:

```python
frontier = opt_results["optimizer_frontier"]
# [
#     {
#         "model": "gpt-3.5",
#         "cost": 1000,
#         "quality": 75,
#         "efficiency": 0.075
#     },
#     {
#         "model": "gpt-4",
#         "cost": 2500,
#         "quality": 92,
#         "efficiency": 0.0368
#     }
# ]
```

### Analyzer Statistics

Failure analysis includes:
- **total_failures**: Total number of failed tasks
- **most_common_errors**: List of error types and frequencies
- **failure_rate**: Percentage of failed tasks
- **recovery_strategies**: Recommended recovery actions

## Configuration

### Custom Models

```python
integration = EdgeSystemIntegrationV2(
    models=["model-a", "model-b", "model-c"]
)
```

### Custom LATTI Home

```python
integration = EdgeSystemIntegrationV2(
    latti_home="/custom/path/.latti"
)
```

## Testing

Run the comprehensive test suite:

```bash
pytest tests/test_edge_system_integration_v2.py -v
```

Test coverage includes:
- ✅ Initialization and configuration
- ✅ Task routing and complexity scoring
- ✅ Execution recording (success and failure)
- ✅ Bandit learning
- ✅ Optimizer frontier computation
- ✅ Failure mode analysis
- ✅ Recovery strategies
- ✅ State persistence
- ✅ Report generation
- ✅ Hook interface
- ✅ Global hook singleton
- ✅ Complete workflows

## Error Handling

The system handles various error types:

```python
# Timeout errors
integration.record_execution(
    task_id="task_1",
    model="gpt-4",
    success=False,
    error_type="timeout",
    error_message="Task exceeded time limit"
)

# Memory errors
integration.record_execution(
    task_id="task_2",
    model="gpt-4",
    success=False,
    error_type="memory_error",
    error_message="Out of memory"
)

# Get recovery strategy
strategy_type, strategy_desc = integration.get_recovery_strategy("task_1")
# Returns: ("retry_with_upgrade", "Retry with gpt-4 instead of gpt-3.5")
```

## Best Practices

1. **Always record execution outcomes** - This enables learning and optimization
2. **Use meaningful task descriptions** - Better descriptions lead to better routing
3. **Monitor failure patterns** - Use analyzer stats to identify systemic issues
4. **Review optimization results regularly** - Adjust model selection based on frontier
5. **Implement recovery strategies** - Use recommended strategies for failed tasks

## Troubleshooting

### No optimization results

Ensure you have recorded at least 3 task executions:

```python
# Record multiple outcomes
for i in range(3):
    integration.record_execution(...)

# Then optimize
opt_results = integration.optimize()
```

### State not persisting

Check that `~/.latti/edge_system_v2/` directory exists and is writable:

```bash
mkdir -p ~/.latti/edge_system_v2/
chmod 755 ~/.latti/edge_system_v2/
```

### Unexpected routing decisions

Check the complexity score and routing metadata:

```python
result = integration.process_task(task)
print(result["routing_metadata"])
```

## Future Enhancements

- [ ] Dynamic model addition/removal
- [ ] Contextual bandit (state-dependent rewards)
- [ ] Multi-objective optimization
- [ ] Predictive failure detection
- [ ] Automated recovery execution
- [ ] Real-time performance dashboards

## References

- Phase 4 Edge System: `edge_system.py`
- Phase 5 Optimization: `bandit.py`, `optimizer.py`, `analyzer.py`
- Test Suite: `tests/test_edge_system_integration_v2.py`
