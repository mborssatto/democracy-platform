"""Pseudonymization at the ingestion boundary.

Real author handles never enter the main tables — they are replaced with a keyed
HMAC pseudonym the moment data arrives. Why HMAC and not a plain hash: platform
user IDs are guessable/enumerable, so a plain SHA-256 can be reversed by hashing
all candidates. With a secret key, the pseudonyms are meaningless without it.

The reid_vault table simulates the separate, restricted mapping store from the
full plan (separate schema + key + audited access function in production).
"""
import base64
import hashlib
import hmac
import os

from . import db


def _key() -> bytes:
    return os.environ.get("PSEUDONYM_KEY", "prototype-dev-key-change-me").encode()


def pseudonymize(real_author_id: str) -> str:
    digest = hmac.new(_key(), real_author_id.encode(), hashlib.sha256).hexdigest()
    return "anon_" + digest[:12]


def store_mapping(con, real_author_id: str, pseudo: str) -> None:
    # Prototype "encryption": base64 stand-in, clearly marked. In production this is
    # real application-layer encryption with its own key. Never rely on encoding.
    fake_encrypted = "DEMO-ENC:" + base64.b64encode(real_author_id.encode()).decode()
    con.execute(
        db.sql_insert_ignore("INTO reid_vault (author_pseudo, real_id_encrypted) VALUES (?,?)"),
        (pseudo, fake_encrypted),
    )


def vault_lookup(con, pseudo: str, justification: str) -> str | None:
    """The ONLY way to re-identify. Every call is written to the audit log."""
    db.audit(con, "system", "reid_vault_access", {"pseudo": pseudo, "justification": justification})
    row = con.execute(
        "SELECT real_id_encrypted FROM reid_vault WHERE author_pseudo=?", (pseudo,)
    ).fetchone()
    if not row:
        return None
    return base64.b64decode(row["real_id_encrypted"].removeprefix("DEMO-ENC:")).decode()
