"""Remove duplicate candidates: exactly (same id) or by embedding similarity."""

from __future__ import annotations

from collections.abc import Sequence

from hone_select._tracing import current_trace
from hone_select.errors import PortError
from hone_select.ports import Embedder
from hone_select.types import Candidate, as_text


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two L2-normalized vectors (their dot product)."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def dedup(
    candidates: list[Candidate],
    method: str = "exact",
    *,
    earlier: Sequence[Candidate] = (),
    embedder: Embedder | None = None,
    threshold: float = 0.95,
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """``(kept, removed)``; each removed entry names the id it duplicates. ``earlier`` candidates are
    already kept (used when candidates arrive one at a time)."""
    if method == "off":
        return candidates, []
    if method == "embedding":
        return _by_embedding(candidates, earlier, embedder, threshold)
    seen = {c.id for c in earlier}
    kept: list[Candidate] = []
    removed: list[tuple[Candidate, str]] = []
    for candidate in candidates:
        if candidate.id in seen:
            removed.append((candidate, candidate.id))
        else:
            seen.add(candidate.id)
            kept.append(candidate)
    return kept, removed


def _by_embedding(
    candidates: list[Candidate], earlier: Sequence[Candidate], embedder: Embedder | None, threshold: float
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    if embedder is None:
        raise ValueError("embedding dedup needs an embedder")  # Engine checks this at construction
    everyone = [*earlier, *candidates]
    vectors = embedder.embed([as_text(c.data) for c in everyone], trace=current_trace())
    if len(vectors) != len(everyone):
        raise PortError(f"embedder returned {len(vectors)} vectors for {len(everyone)} texts")
    kept_vectors = list(zip(earlier, vectors, strict=False))
    kept: list[Candidate] = []
    removed: list[tuple[Candidate, str]] = []
    for candidate, vector in zip(candidates, vectors[len(earlier) :], strict=True):
        match = next((other for other, v in kept_vectors if cosine(vector, v) >= threshold), None)
        if match is not None:
            removed.append((candidate, match.id))
        else:
            kept_vectors.append((candidate, vector))
            kept.append(candidate)
    return kept, removed
