"""Lattice Neural Network — Monte Carlo as hidden layer.

The lattice solver IS a neural network:
  Input layer:  feature vector (team stats, prices, any real-valued features)
  Hidden layer: Monte Carlo sampling weighted by feature importance
  Output layer: predicted probability

No gradient descent. No backprop. The Monte Carlo IS the computation.
Training = updating the cost function weights from observed outcomes.

OPH connection: each feature is an independent observable. The weights
are Lagrange multipliers. The prediction is a partition function ratio.
This is MaxEnt prediction with online learning — the Gibbs state updates
as new data arrives.

Pure Python. Uses the existing solve() from lattice_solver.py.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from .lattice_solver import solve


@dataclass
class PredictResult:
    """Prediction from the lattice neural network."""
    probability: float
    confidence: float
    feature_contributions: dict[str, float]  # how much each feature pulled
    elapsed_ms: float

    def to_text(self) -> str:
        lines = [
            f'Prediction: {self.probability:.4f}',
            f'Confidence: {self.confidence:.4f}',
        ]
        for feat, contrib in sorted(self.feature_contributions.items(),
                                     key=lambda t: abs(t[1]), reverse=True):
            lines.append(f'  {feat}: {contrib:+.4f}')
        lines.append(f'Time: {self.elapsed_ms:.0f}ms')
        return '\n'.join(lines)


class LatticeNN:
    """Neural network where the hidden layer is Monte Carlo sampling.

    The cost function for the lattice solver is:
        cost(x) = sum_i w_i * (x_i - f_i)^2
    where w_i are learned weights and f_i are input features.

    The prediction is the probability that the outcome is 1,
    estimated from how much of the sample mass concentrates
    near the "positive outcome" region of feature space.

    Training: simple online update w += lr * (outcome - predicted) * |feature|.
    This is a one-layer perceptron with Monte Carlo activation.
    """

    def __init__(
        self,
        feature_names: list[str],
        initial_weights: dict[str, float] | None = None,
        learning_rate: float = 0.1,
    ):
        self.feature_names = list(feature_names)
        self.weights = initial_weights or {f: 1.0 for f in feature_names}
        self.bias = 0.0
        self.lr = learning_rate
        self.history: list[tuple[dict[str, float], float, float]] = []  # (features, outcome, predicted)

    def predict(self, features: dict[str, float], samples: int = 2000) -> PredictResult:
        """Run lattice solver with current weights to get probability.

        The solver searches for the point in feature space that minimizes
        the weighted distance to the input. The cost at the minimum,
        relative to a random baseline, gives the probability.
        """
        t0 = time.monotonic()
        dims = len(self.feature_names)
        if dims == 0:
            return PredictResult(0.5, 0.0, {}, 0.0)

        feat_vals = [features.get(f, 0.0) for f in self.feature_names]
        w_vals = [self.weights.get(f, 1.0) for f in self.feature_names]

        # Cost function: weighted distance from input features
        # The solver finds the minimum — how "typical" this input is
        # relative to the learned weight landscape
        def cost_fn(x: list[float]) -> float:
            total = 0.0
            for i in range(dims):
                total += w_vals[i] * (x[i] - feat_vals[i]) ** 2
            return total

        # Bounds: feature values +/- 2 (normalized feature space)
        bounds = [(feat_vals[i] - 2.0, feat_vals[i] + 2.0) for i in range(dims)]

        result = solve(cost_fn, bounds, samples)

        # Convert cost to probability via sigmoid
        # Scale by number of features to keep in reasonable range
        scale = max(1.0, sum(abs(w) for w in w_vals) / dims)
        z = -(result.cost / scale) + self.bias
        probability = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))

        # Feature contributions: how much each weight * feature pulls
        contributions = {}
        total_pull = sum(abs(w_vals[i] * feat_vals[i]) for i in range(dims))
        for i, f in enumerate(self.feature_names):
            if total_pull > 1e-30:
                contributions[f] = w_vals[i] * feat_vals[i] / total_pull
            else:
                contributions[f] = 0.0

        # Confidence from solver convergence and history size
        hist_factor = min(1.0, len(self.history) / 20.0)
        confidence = result.confidence * hist_factor

        elapsed = (time.monotonic() - t0) * 1000
        return PredictResult(
            probability=probability,
            confidence=confidence,
            feature_contributions=contributions,
            elapsed_ms=elapsed,
        )

    def train(self, features: dict[str, float], outcome: float) -> None:
        """Update weights from observed outcome.

        Online gradient: w_i += lr * (outcome - predicted) * |feature_i|
        Bias updates similarly.
        This is a single-layer perceptron update with feature magnitude
        as the gradient signal.
        """
        pred = self.predict(features, samples=500)
        error = outcome - pred.probability

        for f in self.feature_names:
            feat_val = features.get(f, 0.0)
            # Weight update proportional to feature magnitude and error
            self.weights[f] += self.lr * error * abs(feat_val)
            # Clamp weights to prevent divergence
            self.weights[f] = max(-10.0, min(10.0, self.weights[f]))

        self.bias += self.lr * error
        self.bias = max(-5.0, min(5.0, self.bias))

        self.history.append((dict(features), outcome, pred.probability))

    def save(self, path: str) -> None:
        """Save model state to JSON."""
        data = {
            'feature_names': self.feature_names,
            'weights': self.weights,
            'bias': self.bias,
            'lr': self.lr,
            'history_len': len(self.history),
            'last_10': [
                {'features': h[0], 'outcome': h[1], 'predicted': h[2]}
                for h in self.history[-10:]
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2))

    def load(self, path: str) -> None:
        """Load model state from JSON."""
        data = json.loads(Path(path).read_text())
        self.feature_names = data['feature_names']
        self.weights = data['weights']
        self.bias = data.get('bias', 0.0)
        self.lr = data.get('lr', self.lr)

    def status(self) -> str:
        """Human-readable model status."""
        lines = [
            f'LatticeNN: {len(self.feature_names)} features, {len(self.history)} training samples',
            f'Learning rate: {self.lr}',
        ]
        for f in self.feature_names:
            w = self.weights.get(f, 0.0)
            lines.append(f'  {f}: w={w:.4f}')
        if self.history:
            recent = self.history[-5:]
            errors = [abs(h[1] - h[2]) for h in recent]
            lines.append(f'Recent MAE: {sum(errors) / len(errors):.4f}')
        return '\n'.join(lines)
