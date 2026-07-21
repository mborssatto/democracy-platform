"""Gate tests — the compliance-critical logic."""
from app import gates, pseudo


def test_edit_distance_zero_for_identical():
    assert gates.token_edit_distance_pct("a b c", "a b c") == 0.0


def test_edit_distance_full_for_disjoint():
    assert gates.token_edit_distance_pct("a b c", "x y z") == 100.0


def test_edit_gate_blocks_unedited_seed():
    seed = "Ich verstehe, dass das Thema Sorgen macht und der Faktencheck sagt etwas anderes."
    ok, msg, dist = gates.edit_gate(seed, seed)
    assert not ok and dist == 0.0


def test_edit_gate_passes_genuine_rework():
    seed = "Ich verstehe, dass das Thema Sorgen macht und der Faktencheck sagt etwas anderes."
    edited = ("Hey, ich kenne das Gefühl ehrlich gesagt selber gut von meiner Familie. "
              "Guck mal, hier wurde das konkret geprüft und aufgeklärt, fand ich spannend zu lesen.")
    ok, msg, dist = gates.edit_gate(seed, edited)
    assert ok and dist >= 15


def test_neutrality_blocks_electoral_ask():
    ok, _ = gates.neutrality_check("Deshalb: wählt auf keinen Fall diese Partei!")
    assert not ok


def test_neutrality_blocks_party_mention():
    ok, _ = gates.neutrality_check("Die AfD lügt hier ganz offensichtlich.")
    assert not ok


def test_neutrality_allows_factual_correction():
    ok, _ = gates.neutrality_check("Der Faktencheck von Correctiv zeigt: die Zahl stimmt so nicht.")
    assert ok


def test_copypasta_blocks_near_identical():
    text = "Der Faktencheck zeigt dass diese Behauptung falsch ist bitte lesen"
    ok, _ = gates.copypasta_check(text, [text + " danke"])
    assert not ok


def test_copypasta_allows_distinct():
    ok, _ = gates.copypasta_check(
        "Ich habe dazu eine ganz andere Perspektive aus meiner eigenen Erfahrung",
        ["Der Faktencheck zeigt dass diese Behauptung falsch ist"])
    assert ok


def test_rate_cap():
    assert gates.rate_cap_check(4)[0] is True
    assert gates.rate_cap_check(5)[0] is False


def test_pseudonymization_is_keyed_and_stable():
    a = pseudo.pseudonymize("user_123")
    b = pseudo.pseudonymize("user_123")
    c = pseudo.pseudonymize("user_124")
    assert a == b != c
    assert a.startswith("anon_") and "user" not in a


VARIANT_SEED = (
    "Warm: Das Thema bewegt gerade viele Menschen im ganzen Land sehr stark.\n"
    "Factual: Der Faktencheck von Correctiv kommt hier zu einem anderen Ergebnis.\n"
    "Wry: Grosse Behauptungen reisen schneller als ihre Belege es je koennten."
)


def test_picking_a_tone_variant_is_not_an_edit():
    """Choosing one of the seed's tone variants verbatim must read as 0%
    changed — a selection, not a rework (hard rule 2)."""
    picked = "Der Faktencheck von Correctiv kommt hier zu einem anderen Ergebnis."
    assert gates.seed_edit_distance_pct(VARIANT_SEED, picked) == 0.0
    ok, _, dist = gates.edit_gate(VARIANT_SEED, picked)
    assert ok is False
    assert dist == 0.0


def test_reworked_tone_variant_can_pass():
    """A genuine rework of one variant is measured against that variant."""
    rework = ("Schau mal, Correctiv hat das geprueft und widerlegt - ich haenge "
              "den Link an, lies ihn in Ruhe bevor du das weiter teilst bitte.")
    ok, msg, dist = gates.edit_gate(VARIANT_SEED, rework)
    assert dist >= 15.0
    assert ok is True, msg


def test_plain_seed_keeps_old_gate_behavior():
    """Non-variant seeds (legacy drafts) still compare against the whole text."""
    assert gates.seed_variants("just a plain single seed") == ["just a plain single seed"]
    assert gates.seed_edit_distance_pct("one two three", "one two three") == 0.0
