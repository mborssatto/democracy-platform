"""Golden-set eval harness tests (BACKLOG M4.4).

Two things are under test here:
1. The golden set itself — it is versioned data, so its shape and coverage
   are contract-checked (all five narratives, decoys, borderline posts).
2. The runner — `evals.run_evals.main()` must return the score dict CI relies
   on, and mock-mode scores must clear the CI thresholds (they are calibrated
   ~5 points below observed mock performance, so this stays stable offline).

Mock mode is forced by nulling the module-level key attributes on `app.llm`
(the same trick `llm.mode()` keys off), so these tests never hit a paid API
even when a developer has ANTHROPIC_API_KEY in `.env`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402
from evals import run_evals  # noqa: E402


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", None)
    monkeypatch.setattr(llm, "OPENAI_KEY", None)
    assert llm.mode() == "mock"


# ------------------------------------------------------------------ golden set

def test_golden_set_loads_and_is_big_enough():
    golden = run_evals.load_golden_set()
    assert golden["version"]  # versioned, per BACKLOG "per prompt version"
    assert len(golden["entries"]) >= 30


def test_golden_set_entry_shape():
    for entry in run_evals.load_golden_set()["entries"]:
        assert set(entry) == {"id", "text", "label_relevant", "label_narrative"}
        assert isinstance(entry["label_relevant"], bool)
        assert entry["label_narrative"] in set(llm.NARRATIVE_KEYWORDS) | {"none"}
        assert entry["text"].strip()


def test_golden_set_ids_unique():
    ids = [e["id"] for e in run_evals.load_golden_set()["entries"]]
    assert len(ids) == len(set(ids))


def test_golden_set_covers_all_narratives_decoys_and_borderline():
    entries = run_evals.load_golden_set()["entries"]
    labels = {e["label_narrative"] for e in entries}
    assert set(llm.NARRATIVE_KEYWORDS) <= labels, "every tracked narrative needs examples"
    decoys = [e for e in entries if not e["label_relevant"]]
    assert len(decoys) >= 5, "need irrelevant decoys so triage precision is measured"
    borderline = [e for e in entries if e["label_relevant"] and e["label_narrative"] == "none"]
    assert len(borderline) >= 3, "need political-but-untracked posts ('none' class)"


# ---------------------------------------------------------------------- runner

def test_main_returns_expected_score_keys(capsys):
    scores = run_evals.main(write_results=False)
    capsys.readouterr()  # swallow the printed table
    assert {"triage_precision", "triage_recall", "classify_accuracy",
            "per_narrative", "n_entries", "failures"} <= set(scores)
    expected_classes = set(llm.NARRATIVE_KEYWORDS) | {"none"}
    assert set(scores["per_narrative"]) == expected_classes


def test_mock_scores_clear_ci_thresholds(capsys):
    scores = run_evals.main(write_results=False)
    capsys.readouterr()
    assert run_evals.check_thresholds(scores) == []
    # honest golden set: paraphrases without exact keywords keep mock < 100%
    assert scores["classify_accuracy"] < 1.0
