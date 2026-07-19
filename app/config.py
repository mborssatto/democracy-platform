"""Central configuration: every tunable threshold in one place.

Each value reads from an environment variable (via `.env` or the process env)
and falls back to the prototype default — changing a threshold requires no code
edit. Values are resolved at import time, so tests can `monkeypatch.setenv(...)`
and `importlib.reload(config)` to try different settings; modules that use these
knobs read them as `config.NAME` at call time, so a reload is picked up
everywhere without reloading anything else.

Documented (commented out, with defaults) in `.env.example`.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- pipeline (app/pipeline.py) ----------------------------------------------
# Below this reach we NEVER surface content (hard rule: amplification floor).
AMPLIFICATION_FLOOR_VIEWS = _int("AMPLIFICATION_FLOOR_VIEWS", 5000)
# An opportunity is created at/above this score.
SCORE_THRESHOLD = _float("SCORE_THRESHOLD", 40.0)
# Open opportunities expire this many hours after creation.
OPPORTUNITY_EXPIRY_HOURS = _int("OPPORTUNITY_EXPIRY_HOURS", 72)

# --- gates (app/gates.py) ----------------------------------------------------
# Minimum % of the AI seed a human must change (word-level edit distance).
EDIT_GATE_MIN_PCT = _float("EDIT_GATE_MIN_PCT", 15.0)
# Minimum seconds a draft must be reviewed before it can pass the dwell gate.
DWELL_MIN_SECONDS = _int("DWELL_MIN_SECONDS", 60)
# Maximum replies a member may post per day.
RATE_CAP_PER_DAY = _int("RATE_CAP_PER_DAY", 5)
# Jaccard similarity at/above which a reply counts as copypasta.
COPYPASTA_JACCARD_THRESHOLD = _float("COPYPASTA_JACCARD_THRESHOLD", 0.85)


def as_dict() -> dict:
    """Active values, for the API/UI (transparency: show the knobs in use)."""
    return {
        "amplification_floor_views": AMPLIFICATION_FLOOR_VIEWS,
        "score_threshold": SCORE_THRESHOLD,
        "opportunity_expiry_hours": OPPORTUNITY_EXPIRY_HOURS,
        "edit_gate_min_pct": EDIT_GATE_MIN_PCT,
        "dwell_min_seconds": DWELL_MIN_SECONDS,
        "rate_cap_per_day": RATE_CAP_PER_DAY,
        "copypasta_jaccard_threshold": COPYPASTA_JACCARD_THRESHOLD,
    }
