from datetime import UTC, datetime, timedelta

import pytest

from golden_dog.models import Candidate

try:
    from golden_dog.scoring import Enrichment, score
except ModuleNotFoundError:
    Enrichment = score = None


NOW = datetime(2026, 7, 30, 8, 30, tzinfo=UTC)


def sample_candidate(**changes) -> Candidate:
    values = {
        "pool_address": "pool-1",
        "token_address": "token-1",
        "symbol": "DOG",
        "discovered_at": NOW,
        "pool_created_at": NOW - timedelta(minutes=10),
        "liquidity_usd": 20_000,
        "volume_m5_usd": 2_500,
        "buys_m5": 30,
        "sells_m5": 5,
        "price_change_m5_pct": 12,
        "price_usd": 0.01,
    }
    values.update(changes)
    return Candidate(**values)


def safe_enrichment(**changes):
    values = {
        "mint_authority": False,
        "freeze_authority": False,
        "top10_holder_pct": 25,
        "sampled_at": NOW,
    }
    values.update(changes)
    return Enrichment(**values)


@pytest.mark.parametrize(
    ("enrichment_changes", "reason"),
    [
        ({"mint_authority": True}, "mint authority enabled"),
        ({"freeze_authority": True}, "freeze authority enabled"),
        ({"top10_holder_pct": 56}, "top 10 holders exceed 55%"),
    ],
)
def test_hard_gates_reject_unsafe_candidate(enrichment_changes, reason):
    assert score is not None

    decision = score(sample_candidate(), safe_enrichment(**enrichment_changes), now=NOW)

    assert decision.status == "rejected"
    assert reason in decision.reasons


def test_score_at_85_is_alerted_and_includes_advice():
    assert score is not None

    decision = score(sample_candidate(), safe_enrichment(), now=NOW)

    assert decision.score == 85
    assert decision.status == "alerted"
    assert decision.advice.max_position_pct == 5
    assert decision.advice.entry_ceiling_usd == pytest.approx(0.0105)


def test_missing_enrichment_is_rejected():
    assert score is not None

    decision = score(sample_candidate(), None, now=NOW)

    assert decision.status == "rejected"
    assert "critical enrichment unavailable" in decision.reasons
