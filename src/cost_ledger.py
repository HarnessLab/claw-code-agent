"""Cost tracking for API calls. Logs to ~/.latti/memory/cost-ledger.jsonl"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_types import UsageStats


# Pricing per 1M tokens (OpenRouter rates as of 2026-04)
PRICING_RATES = {
    'claude-3-5-sonnet': {
        'input': 3.0,
        'output': 15.0,
        'cache_creation_input': 3.75,
        'cache_read_input': 0.30,
    },
    'claude-3-5-haiku': {
        'input': 0.80,
        'output': 4.0,
        'cache_creation_input': 1.0,
        'cache_read_input': 0.08,
    },
    'claude-3-opus': {
        'input': 15.0,
        'output': 75.0,
        'cache_creation_input': 18.75,
        'cache_read_input': 1.50,
    },
}


def calculate_cost_usd(model: str, usage: UsageStats) -> float:
    """Calculate cost in USD for a single API call."""
    rates = PRICING_RATES.get(model)
    if not rates:
        # Fallback: assume Sonnet pricing for unknown models
        rates = PRICING_RATES['claude-3-5-sonnet']
    
    cost = 0.0
    
    # Input tokens (regular + cache creation)
    input_cost_per_token = rates['input'] / 1_000_000
    cost += usage.input_tokens * input_cost_per_token
    
    # Cache creation input tokens (charged at higher rate)
    if usage.cache_creation_input_tokens > 0:
        cache_creation_cost_per_token = rates['cache_creation_input'] / 1_000_000
        cost += usage.cache_creation_input_tokens * cache_creation_cost_per_token
    
    # Cache read input tokens (charged at lower rate)
    if usage.cache_read_input_tokens > 0:
        cache_read_cost_per_token = rates['cache_read_input'] / 1_000_000
        cost += usage.cache_read_input_tokens * cache_read_cost_per_token
    
    # Output tokens
    output_cost_per_token = rates['output'] / 1_000_000
    cost += usage.output_tokens * output_cost_per_token
    
    return cost


def log_api_call(
    model: str,
    usage: UsageStats,
    session_id: str | None = None,
) -> None:
    """Log an API call to the cost ledger."""
    ledger_path = Path.home() / '.latti' / 'memory' / 'cost-ledger.jsonl'
    cost_usd = calculate_cost_usd(model, usage)

    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'model': model,
        'input_tokens': usage.input_tokens,
        'output_tokens': usage.output_tokens,
        'cache_creation_input_tokens': usage.cache_creation_input_tokens,
        'cache_read_input_tokens': usage.cache_read_input_tokens,
        'reasoning_tokens': usage.reasoning_tokens,
        'cost_usd': round(cost_usd, 6),
        'session_id': session_id,
    }

    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        # Cost logging must never break the chat loop.
        return


def get_session_cost(session_id: str | None = None) -> dict[str, Any]:
    """Aggregate cost for a session."""
    ledger_path = Path.home() / '.latti' / 'memory' / 'cost-ledger.jsonl'
    
    if not ledger_path.exists():
        return {
            'total_cost_usd': 0.0,
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'call_count': 0,
            'by_model': {},
        }
    
    total_cost = 0.0
    total_input = 0
    total_output = 0
    call_count = 0
    by_model: dict[str, dict[str, Any]] = {}
    
    with open(ledger_path) as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            
            # Filter by session if provided
            if session_id and entry.get('session_id') != session_id:
                continue
            
            model = entry.get('model', 'unknown')
            cost = entry.get('cost_usd', 0.0)
            input_tokens = entry.get('input_tokens', 0)
            output_tokens = entry.get('output_tokens', 0)
            
            total_cost += cost
            total_input += input_tokens
            total_output += output_tokens
            call_count += 1
            
            if model not in by_model:
                by_model[model] = {
                    'cost_usd': 0.0,
                    'call_count': 0,
                    'input_tokens': 0,
                    'output_tokens': 0,
                }
            
            by_model[model]['cost_usd'] += cost
            by_model[model]['call_count'] += 1
            by_model[model]['input_tokens'] += input_tokens
            by_model[model]['output_tokens'] += output_tokens
    
    return {
        'total_cost_usd': round(total_cost, 6),
        'total_input_tokens': total_input,
        'total_output_tokens': total_output,
        'call_count': call_count,
        'by_model': by_model,
    }
