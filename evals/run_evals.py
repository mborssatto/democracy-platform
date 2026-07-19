"""Golden-set evals for the triage + classify LLM stages (BACKLOG M4.4).

Runs `app.llm.triage` and `app.llm.classify` over every labeled post in
`evals/golden_set.json` and scores them:

- triage:   precision / recall on the binary `label_relevant`
- classify: overall accuracy + per-narrative precision / recall
            ("none" is scored as its own class)

Works fully offline in mock mode (no API key -> deterministic heuristics);
with ANTHROPIC_API_KEY / OPENAI_API_KEY in `.env` the same script scores the
real model. Every report is stamped with `app.llm.PROMPT_VERSION` and the
golden-set version, so scores are comparable per prompt version over time.

Usage:
    python evals/run_evals.py          # prints a table, writes evals/results/,
                                       # exits 1 if any threshold is missed

The thresholds below are calibrated ~5 points under observed mock-mode
performance: stable in CI, but a regression in the heuristics/prompts fails.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent))  # make `app` importable when run as a script

from app import llm  # noqa: E402

GOLDEN_SET = EVALS_DIR / "golden_set.json"
RESULTS_DIR = EVALS_DIR / "results"

# Observed in mock mode (v1 golden set): triage precision 0.969, recall 0.912,
# classify accuracy 0.762. Fail ~5 points below that so drift is caught early.
THRESHOLDS = {
    "triage_precision": 0.90,
    "triage_recall": 0.85,
    "classify_accuracy": 0.70,
}


def load_golden_set(path: Path = GOLDEN_SET) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["entries"], "golden set is empty"
    return data


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float]:
    """Precision and recall, defined as 1.0 when the denominator is empty."""
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


def evaluate(entries: list[dict]) -> dict:
    """Run triage + classify on every entry and compute all metrics."""
    narratives = list(llm.NARRATIVE_KEYWORDS) + ["none"]

    triage_tp = triage_fp = triage_fn = 0
    classify_correct = 0
    per_class = {c: {"tp": 0, "fp": 0, "fn": 0} for c in narratives}
    failures = []

    for entry in entries:
        predicted_relevant = bool(llm.triage(entry["text"])["relevant"])
        predicted_narrative = str(llm.classify(entry["text"])["narrative"])

        if predicted_relevant and entry["label_relevant"]:
            triage_tp += 1
        elif predicted_relevant and not entry["label_relevant"]:
            triage_fp += 1
        elif not predicted_relevant and entry["label_relevant"]:
            triage_fn += 1

        gold = entry["label_narrative"]
        if predicted_narrative == gold:
            classify_correct += 1
            per_class[gold]["tp"] += 1
        else:
            per_class[gold]["fn"] += 1
            if predicted_narrative in per_class:
                per_class[predicted_narrative]["fp"] += 1
        if predicted_relevant != entry["label_relevant"] or predicted_narrative != gold:
            failures.append({
                "id": entry["id"],
                "text": entry["text"],
                "expected": {"relevant": entry["label_relevant"], "narrative": gold},
                "predicted": {"relevant": predicted_relevant, "narrative": predicted_narrative},
            })

    triage_precision, triage_recall = _prf(triage_tp, triage_fp, triage_fn)
    per_narrative = {}
    for code, c in per_class.items():
        p, r = _prf(c["tp"], c["fp"], c["fn"])
        per_narrative[code] = {"precision": round(p, 3), "recall": round(r, 3),
                               "support": c["tp"] + c["fn"]}

    return {
        "triage_precision": round(triage_precision, 3),
        "triage_recall": round(triage_recall, 3),
        "classify_accuracy": round(classify_correct / len(entries), 3),
        "per_narrative": per_narrative,
        "n_entries": len(entries),
        "failures": failures,
    }


def print_report(scores: dict, golden_version: str) -> None:
    print(f"Golden-set evals  |  llm mode: {llm.mode()}  |  "
          f"prompt: {llm.PROMPT_VERSION}  |  golden set: {golden_version}  |  "
          f"n={scores['n_entries']}")
    print()
    print(f"  triage    precision {scores['triage_precision']:.3f}   "
          f"recall {scores['triage_recall']:.3f}")
    print(f"  classify  accuracy  {scores['classify_accuracy']:.3f}")
    print()
    print(f"  {'narrative':<28} {'precision':>9} {'recall':>7} {'support':>8}")
    for code, m in scores["per_narrative"].items():
        print(f"  {code:<28} {m['precision']:>9.3f} {m['recall']:>7.3f} {m['support']:>8}")
    if scores["failures"]:
        print(f"\n  {len(scores['failures'])} misclassified (ids): "
              + ", ".join(f["id"] for f in scores["failures"]))


def check_thresholds(scores: dict) -> list[str]:
    """Return a list of human-readable threshold violations (empty = pass)."""
    return [
        f"{metric} {scores[metric]:.3f} < threshold {minimum:.2f}"
        for metric, minimum in THRESHOLDS.items()
        if scores[metric] < minimum
    ]


def main(write_results: bool = True) -> dict:
    golden = load_golden_set()
    scores = evaluate(golden["entries"])
    print_report(scores, golden["version"])

    if write_results:
        RESULTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        mode_slug = llm.mode().replace(":", "-")
        out = RESULTS_DIR / f"{stamp}_{mode_slug}.json"
        report = {
            "timestamp": stamp,
            "llm_mode": llm.mode(),
            "prompt_version": llm.PROMPT_VERSION,
            "golden_set_version": golden["version"],
            "thresholds": THRESHOLDS,
            "scores": {k: v for k, v in scores.items() if k != "failures"},
            "failures": scores["failures"],
        }
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  report written: {out.relative_to(EVALS_DIR.parent)}")

    return scores


if __name__ == "__main__":
    violations = check_thresholds(main())
    if violations:
        print("\nFAIL — below threshold:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    print("\nPASS — all thresholds met.")
