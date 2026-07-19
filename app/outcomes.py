"""Outcome snapshots (M4.2): did a posted reply improve the thread climate?

Plan Ch. 9: counterspeech rarely changes the original poster's mind — it shifts
the climate of the thread and the behavior of the silent audience. So after a
member self-reports a posted reply (mark-posted), we schedule thread snapshots
at +1h/+24h/+72h and compare each against a projected no-intervention baseline.
The delta — not reply volume — is the success metric.

PROTOTYPE-HONEST MOCK: like every connector in this repo, the platform re-fetch
is simulated — there is no real thread API here. `_mock_thread_state` derives
plausible numbers DETERMINISTICALLY (hash-seeded, zero randomness, reproducible)
from the original item's metrics. Production replaces that one function with a
real platform re-fetch (docs/integrations/tiktok.md etc.) and scores real
follow-on comments (plan 9.1: Perspective API, hand-validated on German).
Scheduling, storage, and the delta computation stay exactly as they are.

Hard-rule notes: snapshots only READ mocked thread state (no posting path);
snapshot rows carry NO author fields, so the pseudonymization canary holds.
"""
import hashlib

from . import config, db, queue

# The three re-check offsets: label -> seconds after posting.
OFFSETS = {"+1h": 3600, "+24h": 24 * 3600, "+72h": 72 * 3600}

# Mocked reach curve, dampened over time (most engagement happens early).
# The SAME curve applies to intervened and baseline: replying doesn't change
# the thread's reach, it changes the conversation inside it.
VIEW_GROWTH = {"+1h": 1.06, "+24h": 1.32, "+72h": 1.45}
REPLY_GROWTH = {"+1h": 1.10, "+24h": 1.60, "+72h": 1.85}


def _jitter(key: str, scale: float) -> float:
    """Deterministic 'noise' in [-scale, +scale]: a seeded hash, NOT random.
    The same key always yields the same value, so demos and tests reproduce."""
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return scale * ((h % 2001) - 1000) / 1000.0


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def schedule_snapshots(con, posted_action_id: int) -> list[int]:
    """Enqueue the three 'outcome_snapshot' jobs. The queue's run_after gates
    the timing (+1h/+24h/+72h) — no separate scheduler process is needed; the
    worker simply won't claim them before they are due. Caller commits."""
    return [
        queue.enqueue(
            con, "outcome_snapshot",
            {"posted_action_id": posted_action_id, "offset": label},
            run_after=queue.iso_in(seconds),
        )
        for label, seconds in OFFSETS.items()
    ]


def capture_snapshot(con, posted_action_id: int, offset: str) -> dict:
    """Capture one offset: write the observed (intervened) thread state
    (is_baseline=0) AND the projected no-intervention baseline (is_baseline=1).
    Idempotent via UNIQUE(posted_action_id, offset_label, is_baseline) — a
    retried job never duplicates rows. Returns the computed values (tests use
    this to assert determinism). Caller commits."""
    if offset not in OFFSETS:
        raise ValueError(f"unknown snapshot offset '{offset}'")
    src = con.execute(
        """SELECT ci.views, ci.likes, ci.comments
           FROM posted_actions pa
           JOIN drafts d ON d.id = pa.draft_id
           JOIN opportunities o ON o.id = d.opportunity_id
           JOIN content_items ci ON ci.id = o.item_id
           WHERE pa.id=?""", (posted_action_id,)).fetchone()
    if src is None:
        raise ValueError(f"posted action {posted_action_id} not found")

    # MOCK re-fetch — the single line production swaps for a real platform
    # call + real sentiment scoring (see module docstring).
    intervened, baseline = _mock_thread_state(dict(src), posted_action_id, offset)

    for row, is_baseline in ((intervened, 0), (baseline, 1)):
        con.execute(
            db.sql_insert_ignore(
                "INTO outcome_snapshots (posted_action_id, offset_label, captured_at, "
                "views, likes, comments, sentiment_score, reply_count, is_baseline) "
                "VALUES (?,?,?,?,?,?,?,?,?)"),
            (posted_action_id, offset, db.now(), row["views"], row["likes"],
             row["comments"], row["sentiment_score"], row["reply_count"], is_baseline))
    db.audit(con, "worker", "outcome_snapshot_captured", {
        "posted_action": posted_action_id, "offset": offset,
        "sentiment_delta": round(
            intervened["sentiment_score"] - baseline["sentiment_score"], 3),
    })
    return {"intervened": intervened, "baseline": baseline}


def _mock_thread_state(src: dict, posted_action_id: int, offset: str) -> tuple[dict, dict]:
    """Derive both thread states for one offset from the original item's
    metrics — deterministic stand-in for re-fetching the platform thread."""
    seed = f"{posted_action_id}:{offset}"
    growth = VIEW_GROWTH[offset] * (1 + _jitter(seed + ":reach", 0.03))
    views = int(src["views"] * growth)
    likes = int(src["likes"] * growth)
    comments = int(src["comments"] * REPLY_GROWTH[offset]
                   * (1 + _jitter(seed + ":replies", 0.05)))

    # Thread climate (-1..1): a disinformation thread starts negative; the
    # baseline decays slightly (pile-on continues unchallenged) while the
    # intervened thread improves by the modeled lift ± deterministic jitter.
    base = -0.35 + _jitter(seed + ":base", 0.10)
    lift = config.OUTCOME_SENTIMENT_LIFT + _jitter(seed + ":lift", config.OUTCOME_JITTER)
    reach = {"views": views, "likes": likes, "comments": comments, "reply_count": comments}
    intervened = reach | {"sentiment_score": round(_clamp(base + lift), 3)}
    baseline = reach | {"sentiment_score": round(_clamp(base - 0.05), 3)}
    return intervened, baseline


def climate_delta(con, posted_action_id: int) -> dict:
    """Plain-language effect summary: per captured offset, how much better the
    thread climate is vs. the projected no-intervention baseline, plus the
    overall mean. Offsets not captured yet are simply absent."""
    rows = con.execute(
        "SELECT * FROM outcome_snapshots WHERE posted_action_id=? ORDER BY id",
        (posted_action_id,)).fetchall()
    by_offset: dict[str, dict] = {}
    for r in rows:
        by_offset.setdefault(r["offset_label"], {})[r["is_baseline"]] = r
    offsets, deltas = [], []
    for label in OFFSETS:  # stable +1h, +24h, +72h order
        pair = by_offset.get(label, {})
        if 0 not in pair or 1 not in pair:
            continue
        obs, base = pair[0], pair[1]
        delta = round(obs["sentiment_score"] - base["sentiment_score"], 3)
        deltas.append(delta)
        offsets.append({"offset": label, "sentiment_delta": delta,
                        "views": obs["views"], "reply_count": obs["reply_count"]})
    return {
        "offsets": offsets,
        "overall_sentiment_delta":
            round(sum(deltas) / len(deltas), 3) if deltas else None,
    }
