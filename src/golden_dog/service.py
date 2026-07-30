"""Orchestration for read-only pool signal scanning and notification."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from typing import Callable, Iterable, Protocol

from .clients.base import SourceResult
from .models import Candidate, Decision
from .repository import Repository


SOURCE_STALE_AFTER = timedelta(minutes=3)


class Source(Protocol):
    source: str

    async def discover(self) -> SourceResult: ...


class Notifier(Protocol):
    async def notify(self, candidate: Candidate, decision: Decision) -> bool: ...

    async def notify_revocation(self, candidate: Candidate, decision: Decision) -> bool: ...


class SignalService:
    def __init__(
        self,
        repository: Repository,
        sources: Iterable[Source],
        scorer: Callable[..., Decision],
        notifier: Notifier,
    ) -> None:
        self.repository = repository
        self.sources = tuple(sources)
        self.scorer = scorer
        self.notifier = notifier

    async def scan_once(self, now: datetime) -> list[Decision]:
        self.repository.initialize()
        results: list[SourceResult] = []
        for source in self.sources:
            try:
                results.append(await source.discover())
            except Exception as error:
                results.append(SourceResult((), now, source.source, type(error).__name__))
        alerting_allowed = True
        candidates: dict[str, Candidate] = {}
        for source, result in zip(self.sources, results, strict=True):
            error = result.error
            stale = now - result.sampled_at > SOURCE_STALE_AFTER
            if stale and error is None:
                error = "stale"
            self.repository.save_source_health(result.source, result.sampled_at, error)
            if getattr(source, "critical", False) and error is not None:
                alerting_allowed = False
            for candidate in result.items:
                candidates.setdefault(candidate.pool_address, candidate)

        scored: list[tuple[Candidate, Decision]] = []
        for candidate in candidates.values():
            decision = self.scorer(candidate, now=now)
            if inspect.isawaitable(decision):
                decision = await decision
            self.repository.save_decision(decision)
            scored.append((candidate, decision))

        if not alerting_allowed:
            return [decision for _, decision in scored]
        timestamp = int(now.timestamp())
        day = now.date().isoformat()
        qualified = sorted(
            (
                (candidate, decision)
                for candidate, decision in scored
                if decision.status == "alerted"
                and self.repository.can_deliver_alert(candidate.pool_address, timestamp, day)
            ),
            key=lambda item: (-item[1].score, item[0].pool_address),
        )[:3]
        for candidate, decision in qualified:
            owner_token = self.repository.reserve_alert_delivery(candidate.pool_address, timestamp, day)
            if owner_token is None:
                continue
            try:
                delivered = await self.notifier.notify(candidate, decision)
            except Exception:
                self.repository.release_alert_reservation(candidate.pool_address, owner_token)
                continue
            if delivered:
                self.repository.record_alert_delivery(candidate.pool_address, owner_token, timestamp, day)
            else:
                self.repository.release_alert_reservation(candidate.pool_address, owner_token)
        for candidate, decision in scored:
            if decision.status != "rejected":
                continue
            owner_token = self.repository.reserve_revocation(candidate.pool_address, timestamp)
            if owner_token is None:
                continue
            try:
                delivered = await self.notifier.notify_revocation(candidate, decision)
            except Exception:
                self.repository.release_revocation_reservation(candidate.pool_address, owner_token)
                continue
            if delivered:
                self.repository.record_revocation_delivery(candidate.pool_address, owner_token, timestamp)
            else:
                self.repository.release_revocation_reservation(candidate.pool_address, owner_token)
        return [decision for _, decision in scored]
