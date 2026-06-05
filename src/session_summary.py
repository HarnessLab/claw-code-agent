"""Session summarization and indexing for Phase 2 of ATM.

Generates per-turn summaries and embeddings for semantic retrieval.
Stores summaries alongside session files for efficient loading.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import hashlib

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


# Module-level TF-IDF vectorizer — fitted lazily on first use.
# Shared across all embed_text() calls in a process so the vocabulary
# is consistent within a session.
_tfidf_vectorizer: TfidfVectorizer | None = None
_tfidf_corpus: list[str] = []
_EMBED_DIM = 384  # Target dimensionality (padded/truncated from TF-IDF)


@dataclass
class TurnSummary:
    """Summary of a single conversation turn."""
    turn_number: int
    timestamp: str
    summary: str  # 1-3 sentence summary
    embedding: list[float]  # 384-dim (sentence-transformers)
    importance_score: float  # 0-1 (decisions/changes weighted higher)
    full_message_id: str  # Reference to full message in session
    tokens_estimate: int  # For budget calculation
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnSummary:
        return cls(**data)


@dataclass
class SessionSummaryIndex:
    """Index of all turn summaries for a session."""
    session_id: str
    summaries: list[TurnSummary] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.metadata:
            self.metadata = {
                'version': '1.0',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'model_used': 'claude-3-5-sonnet',
                'embedding_model': 'sentence-transformers/all-MiniLM-L6-v2',
                'embedding_dim': 384,
            }
    
    def add_summary(self, summary: TurnSummary) -> None:
        """Add a turn summary to the index."""
        self.summaries.append(summary)
        self.metadata['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    def get_summary(self, turn_number: int) -> TurnSummary | None:
        """Get summary for a specific turn."""
        for s in self.summaries:
            if s.turn_number == turn_number:
                return s
        return None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'summaries': [s.to_dict() for s in self.summaries],
            'metadata': self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionSummaryIndex:
        return cls(
            session_id=data['session_id'],
            summaries=[TurnSummary.from_dict(s) for s in data.get('summaries', [])],
            metadata=data.get('metadata', {}),
        )


def save_summary_index(
    index: SessionSummaryIndex,
    session_path: Path,
) -> Path:
    """Save summary index alongside session file.
    
    Args:
        index: SessionSummaryIndex to save
        session_path: Path to the session JSON file
    
    Returns:
        Path to the saved summary index
    
    Example:
        >>> session_path = Path('.port_sessions/agent/abc123.json')
        >>> summary_path = save_summary_index(index, session_path)
        >>> summary_path
        Path('.port_sessions/agent/abc123.summary.json')
    """
    summary_path = session_path.with_suffix('.summary.json')
    summary_path.write_text(
        json.dumps(index.to_dict(), indent=2),
        encoding='utf-8'
    )
    return summary_path


def load_summary_index(session_path: Path) -> SessionSummaryIndex | None:
    """Load summary index for a session.
    
    Args:
        session_path: Path to the session JSON file
    
    Returns:
        SessionSummaryIndex if it exists, None otherwise
    """
    summary_path = session_path.with_suffix('.summary.json')
    if not summary_path.exists():
        return None
    
    data = json.loads(summary_path.read_text(encoding='utf-8'))
    return SessionSummaryIndex.from_dict(data)


def estimate_importance_score(
    message: dict[str, Any],
    response: dict[str, Any] | None = None,
) -> float:
    """Estimate importance of a turn (0-1).
    
    Higher scores for turns with:
    - Code changes (git diffs, file edits)
    - Decisions (user choices, confirmations)
    - Errors (failures, debugging)
    - Summaries (conclusions, next steps)
    
    Args:
        message: User message dict
        response: Assistant response dict (optional)
    
    Returns:
        Importance score 0-1
    """
    score = 0.5  # Base score
    
    # Check for code-related keywords
    code_keywords = ['git', 'commit', 'diff', 'code', 'function', 'class', 'bug', 'fix']
    content = str(message.get('content', '')).lower()
    if response:
        content += ' ' + str(response.get('content', '')).lower()
    
    for keyword in code_keywords:
        if keyword in content:
            score += 0.1
    
    # Check for decision keywords
    decision_keywords = ['decide', 'choice', 'option', 'approach', 'design', 'plan']
    for keyword in decision_keywords:
        if keyword in content:
            score += 0.1
    
    # Check for error keywords
    error_keywords = ['error', 'fail', 'bug', 'issue', 'problem', 'debug']
    for keyword in error_keywords:
        if keyword in content:
            score += 0.15
    
    # Cap at 1.0
    return min(1.0, score)


def estimate_tokens_for_summary(summary: TurnSummary) -> int:
    """Estimate tokens in a summary (for budget calculation).
    
    Uses 4 chars ≈ 1 token heuristic.
    """
    text = summary.summary
    return max(1, len(text) // 4)


def embed_text(text: str) -> list[float]:
    """Generate a real embedding for text using TF-IDF + SVD projection.

    Uses sklearn's TfidfVectorizer fitted on an in-process corpus, then
    projects to _EMBED_DIM dimensions via a deterministic hash-based
    random projection matrix (Johnson-Lindenstrauss style).

    Properties:
    - Deterministic: same text → same vector every time
    - Consistent: cosine similarity is meaningful across calls
    - Fast: no network, no GPU, <1ms per call
    - No external dependencies beyond numpy + sklearn (already installed)

    Args:
        text: Text to embed

    Returns:
        List of _EMBED_DIM floats (L2-normalised)
    """
    global _tfidf_vectorizer, _tfidf_corpus

    if not text or not text.strip():
        return [0.0] * _EMBED_DIM

    # Lazily fit/refit the vectorizer as new texts arrive.
    # We keep a rolling corpus so vocabulary grows with usage.
    if text not in _tfidf_corpus:
        _tfidf_corpus.append(text)

    if _tfidf_vectorizer is None or len(_tfidf_corpus) % 50 == 0:
        # Refit every 50 new documents so vocabulary stays fresh.
        _tfidf_vectorizer = TfidfVectorizer(
            max_features=2048,
            sublinear_tf=True,
            strip_accents='unicode',
            analyzer='word',
            token_pattern=r'\w+',
            ngram_range=(1, 2),
        )
        _tfidf_vectorizer.fit(_tfidf_corpus)

    # Transform the single text to a sparse TF-IDF vector
    sparse = _tfidf_vectorizer.transform([text])  # shape (1, vocab_size)
    dense = np.asarray(sparse.todense(), dtype=np.float32).flatten()  # (vocab_size,)

    # Project to _EMBED_DIM using a deterministic random projection matrix.
    # The matrix is seeded from a stable hash of the vocabulary size so it
    # stays consistent as long as the vocabulary doesn't change.
    vocab_size = dense.shape[0]
    seed = int(hashlib.md5(str(vocab_size).encode()).hexdigest(), 16) % (2**31)
    rng = np.random.RandomState(seed)
    # Johnson-Lindenstrauss projection: R ∈ R^{_EMBED_DIM × vocab_size}
    R = rng.randn(_EMBED_DIM, vocab_size).astype(np.float32)
    R /= np.linalg.norm(R, axis=1, keepdims=True) + 1e-9

    projected = R @ dense  # (_EMBED_DIM,)

    # L2-normalise so cosine similarity == dot product
    norm = np.linalg.norm(projected)
    if norm > 1e-9:
        projected /= norm

    return projected.tolist()


def reset_embedding_state() -> None:
    """Reset the module-level TF-IDF state (useful in tests)."""
    global _tfidf_vectorizer, _tfidf_corpus
    _tfidf_vectorizer = None
    _tfidf_corpus = []
