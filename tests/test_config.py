"""Config tests: env overrides work, and gates/pipeline follow config defaults.

Pattern: monkeypatch the env var, `importlib.reload(app.config)` so the
module-level constants are re-read. gates/pipeline access values as
`config.NAME` at call time, so the reload is visible without reloading them.
The fixture undoes the env AND reloads once more in teardown so every other
test sees the stock defaults.
"""
import importlib

import pytest

from app import config, gates, pipeline


@pytest.fixture
def set_config(monkeypatch):
    """set_config(NAME=value, ...) → reloaded config module. Restores defaults after."""
    def _set(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, str(value))
        importlib.reload(config)
        return config
    yield _set
    monkeypatch.undo()          # remove the env overrides first...
    importlib.reload(config)    # ...then re-read so defaults are back


def test_defaults_match_previous_hardcoded_values():
    assert config.as_dict() == {
        "amplification_floor_views": 5000,
        "score_threshold": 40.0,
        "opportunity_expiry_hours": 72,
        "edit_gate_min_pct": 15.0,
        "dwell_min_seconds": 60,
        "rate_cap_per_day": 5,
        "copypasta_jaccard_threshold": 0.85,
    }


def test_env_override_reflected_in_as_dict(set_config):
    cfg = set_config(AMPLIFICATION_FLOOR_VIEWS=99999, SCORE_THRESHOLD=70.5)
    assert cfg.AMPLIFICATION_FLOOR_VIEWS == 99999
    assert cfg.as_dict()["amplification_floor_views"] == 99999
    assert cfg.as_dict()["score_threshold"] == 70.5


def test_rate_cap_follows_config_default(set_config):
    set_config(RATE_CAP_PER_DAY=2)
    assert gates.rate_cap_check(1)[0] is True
    assert gates.rate_cap_check(2)[0] is False      # no explicit cap → config
    assert gates.rate_cap_check(2, cap=5)[0] is True  # explicit param still wins


def test_copypasta_threshold_follows_config(set_config):
    a = "der faktencheck zeigt dass diese zahl nicht stimmt"
    b = "der faktencheck zeigt dass diese zahl leider falsch ist"
    assert gates.copypasta_check(a, [b])[0] is True   # distinct at default 0.85
    set_config(COPYPASTA_JACCARD_THRESHOLD=0.3)
    assert gates.copypasta_check(a, [b])[0] is False  # blocked at stricter 0.3


def test_amplification_floor_follows_config(set_config):
    item = {"views": 6000, "views_prev": 1000, "author_followers_band": "10k-100k"}
    score, breakdown = pipeline._score(item, {"confidence": 0.9}, True)
    assert score > 0 and "blocked" not in breakdown   # above default 5000 floor
    set_config(AMPLIFICATION_FLOOR_VIEWS=99999)
    score, breakdown = pipeline._score(item, {"confidence": 0.9}, True)
    assert score == 0.0 and "blocked" in breakdown    # now below the floor


def test_fixture_restores_defaults(set_config):
    set_config(RATE_CAP_PER_DAY=1)
    assert config.RATE_CAP_PER_DAY == 1
    # teardown runs after this test; the next test asserts restoration


def test_defaults_restored_after_override():
    assert config.RATE_CAP_PER_DAY == 5
    assert config.AMPLIFICATION_FLOOR_VIEWS == 5000
