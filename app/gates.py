"""The hard gates of the Democracy Bot.

Every check returns (passed: bool, detail: str). A draft becomes post-ready only
when ALL checks pass. Results are stored in compliance_checks and audit_log —
the evidence chain that a human genuinely controlled the content.
"""
import re
import time

from . import config, llm

# --- edit distance ------------------------------------------------------------

# Reply seeds carry three tone variants ("Warm: …\nFactual: …\nWry: …"); the
# editor's tone buttons let the human pick one. Gates measure against the
# CLOSEST variant — picking a tone is a selection, not a rework (hard rule 2),
# so an unedited variant reads as 0% changed, never as a 60% "edit".
_VARIANT_RE = re.compile(r"^[ \t]*(warm|factual|wry)[ \t]*:[ \t]*", re.IGNORECASE | re.MULTILINE)


def seed_variants(seed: str) -> list[str]:
    """The seed's tone variants, or [seed] when it isn't in variant format."""
    matches = list(_VARIANT_RE.finditer(seed))
    if len(matches) < 2:
        return [seed]
    return [
        seed[m.end():(matches[i + 1].start() if i + 1 < len(matches) else len(seed))].strip()
        for i, m in enumerate(matches)
    ]


def seed_edit_distance_pct(seed: str, current: str) -> float:
    """Edit distance against the closest seed variant (see _VARIANT_RE note)."""
    return min(token_edit_distance_pct(v, current) for v in seed_variants(seed))


def token_edit_distance_pct(seed: str, current: str) -> float:
    """Normalized word-level Levenshtein distance (0..100%).

    'Edit distance' = the minimum number of word insertions/deletions/replacements
    to turn one text into the other, as a share of the longer text.
    """
    a, b = seed.split(), current.split()
    if not a and not b:
        return 0.0
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return round(100.0 * prev[n] / max(m, n), 1)


# --- individual checks --------------------------------------------------------

# Neutrality: German charity law forbids party-electoral campaigning.
ELECTORAL_PATTERNS = [
    r"\bwähl(t|e|en)?\b", r"\bstimm(t|e|en)? (für|gegen)\b", r"\bkeine stimme\b",
    r"\bvote (for|against)\b", r"\berst- ?und zweitstimme\b",
    r"\b(afd|cdu|csu|spd|grüne|fdp|linke|bsw)\b",
]

def neutrality_check(text: str) -> tuple[bool, str]:
    t = text.lower()
    for pat in ELECTORAL_PATTERNS:
        if re.search(pat, t):
            return False, f"Electoral/party language detected (pattern: {pat}). Counter narratives, never campaign."
    return True, "No electoral asks or party references."


def copypasta_check(text: str, recent_posted_texts: list[str],
                    threshold: float | None = None) -> tuple[bool, str]:
    """Near-identical texts from many accounts = what platforms ban as coordination.
    Prototype metric: word-set Jaccard similarity (plan: embedding cosine, flag-for-review)."""
    if threshold is None:
        threshold = config.COPYPASTA_JACCARD_THRESHOLD
    words = set(text.lower().split())
    for other in recent_posted_texts:
        ow = set(other.lower().split())
        if not words or not ow:
            continue
        jaccard = len(words & ow) / len(words | ow)
        if jaccard >= threshold:
            return False, f"Too similar ({jaccard:.0%}) to a reply another member already posted."
    return True, "Distinct from recent community replies."


def edit_gate(seed: str, current: str, min_pct: float | None = None) -> tuple[bool, str, float]:
    if min_pct is None:
        min_pct = config.EDIT_GATE_MIN_PCT
    # Compare against the closest tone variant, not the whole 3-variant seed —
    # otherwise picking one variant alone would clear the gate as a "60% edit".
    closest = min(seed_variants(seed), key=lambda v: token_edit_distance_pct(v, current))
    dist = token_edit_distance_pct(closest, current)
    if dist < min_pct:
        return False, f"Only {dist}% changed (minimum {min_pct}%). Make it yours.", dist
    verdict = llm.substantive_change(closest, current)
    if not verdict["substantive"]:
        return False, verdict["reason"], dist
    return True, f"{dist}% changed; substantive rework confirmed.", dist


def dwell_check(draft_created_at: str, min_seconds: int | None = None) -> tuple[bool, str]:
    if min_seconds is None:
        min_seconds = config.DWELL_MIN_SECONDS
    created = time.mktime(time.strptime(draft_created_at, "%Y-%m-%dT%H:%M:%S"))
    elapsed = int(time.time() - created)
    if elapsed < min_seconds:
        return False, f"Only {elapsed}s of review time (minimum {min_seconds}s). Read the thread again."
    return True, f"{elapsed}s review time."


def rate_cap_check(posted_today: int, cap: int | None = None) -> tuple[bool, str]:
    if cap is None:
        cap = config.RATE_CAP_PER_DAY
    if posted_today >= cap:
        return False, f"Daily cap reached ({cap} replies/day). Quality over volume — see you tomorrow."
    return True, f"{posted_today}/{cap} replies used today."
