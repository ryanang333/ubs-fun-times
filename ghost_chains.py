from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Iterable


LOOKBACK = timedelta(hours=24)

# Phase 1 deliberately uses stable bands. Later phases can add signals within
# these bands without changing the structural ordering.
ISOLATED_SCORE = 0.0
EXTENSION_SCORE = 0.15
CONVERGENCE_SCORE = 0.45
RETURN_SCORE = 0.70
MULTI_LOOP_SCORE = 0.95


@dataclass(frozen=True)
class ActiveTransaction:
    transaction: dict[str, Any]
    created_at: datetime


class GhostChainsProcessor:
    """Maintain a 24-hour directed transaction graph and score new edges."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active: list[ActiveTransaction] = []
        self._scores_by_tx_id: dict[str, float] = {}
        self._watermark: datetime | None = None

    def reset(self) -> None:
        """Restore all graph, cache, and derived state to startup state."""
        with self._lock:
            self._active.clear()
            self._scores_by_tx_id.clear()
            self._watermark = None

    def process_batch(
        self, transactions: Iterable[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Atomically process a request's transactions in input order."""
        results: list[dict[str, Any]] = []
        with self._lock:
            for transaction in transactions:
                tx_id = transaction["txId"]
                if tx_id in self._scores_by_tx_id:
                    results.append(
                        {"txId": tx_id, "riskScore": self._scores_by_tx_id[tx_id]}
                    )
                    continue

                created_at = parse_timestamp(transaction["createdAt"])
                self._advance_window(created_at)
                adjacency = self._build_adjacency()
                score = self._score(transaction, adjacency)

                self._scores_by_tx_id[tx_id] = score
                # Transactions older than the current active window are scored
                # and remembered for idempotency, but cannot alter current state.
                if self._watermark is not None and created_at >= self._watermark - LOOKBACK:
                    self._active.append(
                        ActiveTransaction(deepcopy(transaction), created_at)
                    )
                results.append({"txId": tx_id, "riskScore": score})

        return results

    def _advance_window(self, created_at: datetime) -> None:
        if self._watermark is None or created_at > self._watermark:
            self._watermark = created_at

        cutoff = self._watermark - LOOKBACK
        self._active = [item for item in self._active if item.created_at >= cutoff]

    def _build_adjacency(self) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for item in self._active:
            transaction = item.transaction
            adjacency[transaction["fromUserId"]].add(transaction["toUserId"])
        return adjacency

    def _score(
        self, transaction: dict[str, Any], adjacency: dict[str, set[str]]
    ) -> float:
        source = transaction["fromUserId"]
        target = transaction["toUserId"]
        nodes = set(adjacency)
        nodes.update(node for neighbours in adjacency.values() for node in neighbours)

        # Adding source -> target returns to an upstream node and closes a loop.
        if source == target or _is_reachable(adjacency, target, source):
            # Closing another loop onto a node already participating in a cycle
            # is the Phase 1 multi-loop signal.
            if _node_is_in_cycle(adjacency, target):
                return MULTI_LOOP_SCORE
            return RETURN_SCORE

        # Convergence exists when an ancestor of the source already has a
        # separate route to the proposed target.
        reverse = _reverse_graph(adjacency)
        for ancestor in _reachable_nodes(reverse, source):
            if ancestor != source and _is_reachable(adjacency, ancestor, target):
                return CONVERGENCE_SCORE

        if source in nodes or target in nodes:
            return EXTENSION_SCORE
        return ISOLATED_SCORE


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and normalize it to UTC."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("createdAt must include a timezone")
    return parsed.astimezone(timezone.utc)


def _is_reachable(
    adjacency: dict[str, set[str]], start: str, destination: str
) -> bool:
    return destination in _reachable_nodes(adjacency, start)


def _reachable_nodes(adjacency: dict[str, set[str]], start: str) -> set[str]:
    visited: set[str] = set()
    pending = list(adjacency.get(start, ()))
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, ()))
    return visited


def _reverse_graph(adjacency: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    return reverse


def _node_is_in_cycle(adjacency: dict[str, set[str]], node: str) -> bool:
    return node in _reachable_nodes(adjacency, node)


processor = GhostChainsProcessor()
