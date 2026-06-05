"""
GSM8K benchmark suite.

GSM8K (Grade School Math 8K) is a dataset of 8,500 linguistically diverse
grade-school-level math word problems that require multi-step reasoning.

Paper: https://arxiv.org/abs/2110.14168
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .base import BenchmarkResult, BenchmarkSuite

# ---------------------------------------------------------------------------
# Built-in mini dataset (15 representative problems)
# ---------------------------------------------------------------------------

_BUILTIN_PROBLEMS: list[dict[str, Any]] = [
    {
        "id": "gsm8k-001",
        "question": "Janet's ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?",
        "answer": "18",
    },
    {
        "id": "gsm8k-002",
        "question": "A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total does it take?",
        "answer": "3",
    },
    {
        "id": "gsm8k-003",
        "question": "Josh decides to try flipping a house. He buys a house for $80,000 and then puts in $50,000 in repairs. This increased the value of the house by 150%. How much profit did he make?",
        "answer": "70000",
    },
    {
        "id": "gsm8k-004",
        "question": "James decides to run 3 sprints 3 times a week. He runs 60 meters each sprint. How many total meters does he run a week?",
        "answer": "540",
    },
    {
        "id": "gsm8k-005",
        "question": "Every day, Wendi feeds each of her chickens three cups of mixed chicken feed, containing seeds, mealworms and vegetables to help keep them healthy. She gives the chickens their feed in three separate meals. In the morning, she gives her flock of chickens 15 cups of feed. In the afternoon, she gives her chickens another 25 cups of feed. If Wendi's flock eats a combined total of 15 cups of feed in the final meal of the day, how many chickens does Wendi have?",
        "answer": "20",
    },
    {
        "id": "gsm8k-006",
        "question": "Kylar went to the store to get water and some batteries. He spent $10 on the water and three times that on batteries. How much did he spend total?",
        "answer": "40",
    },
    {
        "id": "gsm8k-007",
        "question": "Toulouse has twice as many sheep as Charleston. Charleston has 4 times as many sheep as Seattle. How many sheep do Toulouse, Charleston, and Seattle have together if Seattle has 20 sheep?",
        "answer": "260",
    },
    {
        "id": "gsm8k-008",
        "question": "Carla is downloading a 200 GB file. Normally she can download 2 GB/minute, but 40% of the way through the download, Windows forces a restart to install updates, which takes 20 minutes. Then Carla has to restart the download from the beginning. How long does it take to download the file?",
        "answer": "160",
    },
    {
        "id": "gsm8k-009",
        "question": "John drives for 3 hours at a speed of 60 mph and then turns around because he realizes he forgot something very important at home. He tries to get home in 4 hours but spends the first 2 hours in standstill traffic. He spends the rest of the time driving at a speed of 30 mph. How far is he from home?",
        "answer": "120",
    },
    {
        "id": "gsm8k-010",
        "question": "Eliza's rate per hour for the first 40 hours she works each week is $10. She also receives an overtime pay of 1.2 times her regular hourly rate. If Eliza worked for 45 hours this week, how much are her earnings for this week?",
        "answer": "460",
    },
    {
        "id": "gsm8k-011",
        "question": "A new program had 60 downloads in the first month. The number of downloads in the second month was three times as many as the downloads in the first month, but then reduced by 30% in the third month. How many downloads did the program have total over the three months?",
        "answer": "366",
    },
    {
        "id": "gsm8k-012",
        "question": "Thomas has 6 more apples than friends. He also has twice as many friends as bags. If he has 20 bags, how many apples does he have?",
        "answer": "46",
    },
    {
        "id": "gsm8k-013",
        "question": "A store sells pencils in packs of 8. If a teacher needs 120 pencils for her class, how many packs does she need to buy?",
        "answer": "15",
    },
    {
        "id": "gsm8k-014",
        "question": "Sam bought a dozen boxes, each with 30 highlighter pens inside, for $10 each box. He rearranged five of these boxes into packages of six highlighters each and sold them for $3 per package. He sold the rest of the highlighters separately at the rate of three pens for $2. How much profit did Sam make in total, in dollars?",
        "answer": "115",
    },
    {
        "id": "gsm8k-015",
        "question": "There are 15 trees in the grove. Grove workers will plant trees in the grove today. After they are done, there will be 21 trees. How many trees did the grove workers plant today?",
        "answer": "6",
    },
]


def _extract_number(text: str) -> str | None:
    """Extract the final numeric answer from agent output.

    Only fires when the output looks like a real model response, not an
    error message.  This prevents backend error noise (e.g. 'total_tokens=0')
    from being mistaken for math answers.
    """
    # Bail on known error patterns before extracting
    if any(marker in text for marker in [
        'backend_error', 'HTTP 4', 'HTTP 5', 'stop_reason=', 'total_tokens=',
        '401', '403', '404', '500', 'Authentication', 'Invalid API',
    ]):
        return None

    text = text.replace(',', '').replace('$', '').strip()
    # Prefer answers after common answer markers
    for marker in ['####', 'answer is', 'answer:', 'the answer', '= ', '==']:
        idx = text.lower().rfind(marker)
        if idx != -1:
            tail = text[idx + len(marker):].strip()
            numbers = re.findall(r'-?\d+\.?\d*', tail)
            if numbers:
                return numbers[0]
    # Fall back to last number in text
    numbers = re.findall(r'-?\d+\.?\d*', text)
    return numbers[-1] if numbers else None


class GSM8KBenchmark(BenchmarkSuite):
    """GSM8K: grade school math word problems."""

    name = "GSM8K"
    description = "Grade school math word problems requiring multi-step reasoning"
    category = "math"

    def load_dataset(self) -> list[dict[str, Any]]:
        jsonl_path = Path(self.data_dir) / "gsm8k.jsonl"
        if jsonl_path.exists():
            problems: list[dict[str, Any]] = []
            with open(jsonl_path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        problems.append(json.loads(line))
            if self.verbose:
                print(f"  Loaded {len(problems)} problems from {jsonl_path}")
            return problems

        if self.verbose:
            print(f"  {jsonl_path} not found — using built-in 15-problem subset")
        return list(_BUILTIN_PROBLEMS)

    def build_prompt(self, problem: dict[str, Any]) -> str:
        question = problem["question"]
        return (
            f"Solve the following grade school math problem step by step. "
            f"Write ONLY the final numerical answer (just the number) to a file called answer.txt.\n\n"
            f"Problem: {question}\n\n"
            f"Save just the number to answer.txt — no units, no explanation."
        )

    def recover_output_files(
        self,
        problem: dict[str, Any],
        workspace: str,
        agent_output: str,
        metadata: dict[str, Any],
    ) -> None:
        del problem
        answer_path = Path(workspace) / "answer.txt"
        if answer_path.exists():
            return
        number = _extract_number(agent_output)
        if number is not None:
            answer_path.write_text(number + "\n", encoding="utf-8")
            metadata["recovered_answer_from_output"] = True

    def evaluate(self, problem: dict[str, Any], workspace: str) -> BenchmarkResult:
        pid = problem.get("id", "unknown")
        expected = str(problem["answer"])
        answer_file = os.path.join(workspace, "answer.txt")

        if not os.path.exists(answer_file):
            return BenchmarkResult(
                problem_id=pid, passed=False, expected=expected,
                error="answer.txt not found",
            )

        with open(answer_file) as fh:
            actual_raw = fh.read().strip()

        actual_num = _extract_number(actual_raw)
        expected_num = _extract_number(expected)

        if actual_num is None:
            return BenchmarkResult(
                problem_id=pid, passed=False,
                expected=expected, actual=actual_raw,
                error=f"Could not extract number from: {actual_raw}",
            )

        try:
            passed = abs(float(actual_num) - float(expected_num)) < 1e-6
        except (ValueError, TypeError):
            passed = actual_num == expected_num

        return BenchmarkResult(
            problem_id=pid, passed=passed,
            expected=expected, actual=actual_raw,
            error="" if passed else f"expected={expected}, got={actual_raw}",
        )
