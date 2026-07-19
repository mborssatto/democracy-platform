"""Local session auth STUB — dev-only, replaced by Supabase Auth in production.

⚠ HOW THIS STUB WORKS (and why it is safe to be this simple):
- Three demo users are seeded at startup: admin / staff / responder. The
  username doubles as the "password" — there are NO real passwords anywhere,
  nothing to hash, nothing to leak. Anyone who can reach the dev server can
  log in as any role; that is the point of a demo stub.
- POST /api/login hands out a random bearer token (24h expiry) stored in the
  `sessions` table; every protected endpoint reads `Authorization: Bearer <t>`.

PRODUCTION SWAP (documented in docs/integrations/supabase.md → "Auth swap"):
Supabase Auth issues real JWTs after email/SSO login. `get_user()` below then
verifies the JWT signature with the project's JWT secret instead of looking the
token up in `sessions`; roles move to a `profiles` table (or user_metadata),
and Postgres Row Level Security enforces the same scoping rules again at the
database layer as defense-in-depth. The route guards in app/main.py stay.

Roles:
- admin      — everything (incl. future admin overrides).
- staff      — everything operational: Radar, Studio, audit, ingestion, pipeline.
- responder  — volunteers: only the Respond flow, and only their own
               claimed opportunities/drafts.
"""
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import db

ROLES = ("admin", "staff", "responder")
DEMO_USERS = (("admin", "admin"), ("staff", "staff"), ("responder", "responder"))
SESSION_HOURS = 24

router = APIRouter()


def seed_demo_users(con) -> None:
    """Idempotent: insert the three demo users if missing (both backends)."""
    for username, role in DEMO_USERS:
        con.execute(
            db.sql_insert_ignore("INTO users (username, role, created_at) VALUES (?,?,?)"),
            (username, role, db.now()))
    con.commit()


def _expiry() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S",
                         time.localtime(time.time() + SESSION_HOURS * 3600))


# ------------------------------------------------------------------ dependencies
def _lookup_token(token: str) -> dict | None:
    """Return {username, role, token} for a live session token, else None."""
    con = db.connect()
    row = con.execute(
        "SELECT s.expires_at, u.username, u.role FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)).fetchone()
    con.close()
    if not row or row["expires_at"] <= db.now():  # ISO strings compare correctly
        return None
    return {"username": row["username"], "role": row["role"], "token": token}


def _bearer_token(request: Request) -> str | None:
    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        return None
    return authz[7:].strip()


def get_user(request: Request) -> dict:
    """FastAPI dependency: the logged-in user, or a clean JSON 401.
    Production swap: verify a Supabase JWT here instead of the sessions table."""
    token = _bearer_token(request)
    user = _lookup_token(token) if token else None
    if not user:
        raise HTTPException(401, "Not logged in (or session expired). "
                                 "Log in and send Authorization: Bearer <token>.")
    return user


def get_user_optional(request: Request) -> dict | None:
    """Like get_user, but returns None instead of raising (used by /api/state
    so the frontend can restore a stored session without a 401 loop)."""
    token = _bearer_token(request)
    return _lookup_token(token) if token else None


def require_role(*roles: str):
    """Dependency factory: 403 (audited) unless the user's role is allowed."""
    def dep(request: Request, user: dict = Depends(get_user)) -> dict:
        if user["role"] not in roles:
            con = db.connect()
            db.audit(con, user["username"], "access_denied",
                     {"path": request.url.path, "role": user["role"],
                      "required": list(roles)})
            con.commit(); con.close()
            raise HTTPException(403, f"Your role ({user['role']}) cannot access this. "
                                     f"Requires: {' or '.join(roles)}.")
        return user
    return dep


def deny_scoped(con, user: dict, action: str, detail: dict) -> None:
    """Audited 403 for data-scoping violations (e.g. a responder touching
    someone else's draft). Commits the audit entry, then raises."""
    db.audit(con, user["username"], "access_denied", {"action": action} | detail)
    con.commit(); con.close()
    raise HTTPException(403, "This item belongs to another responder.")


# ------------------------------------------------------------------ endpoints
class LoginIn(BaseModel):
    username: str


@router.post("/api/login")
def login(body: LoginIn):
    """Dev stub: the username IS the credential — no passwords exist.
    Production: this endpoint disappears; Supabase Auth issues JWTs instead."""
    con = db.connect()
    u = con.execute("SELECT id, username, role FROM users WHERE username = ?",
                    (body.username,)).fetchone()
    if not u:
        db.audit(con, body.username, "login_denied", {"reason": "unknown user"})
        con.commit(); con.close()
        raise HTTPException(401, "Unknown user. Demo users: admin, staff, responder.")
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) "
                "VALUES (?,?,?,?)", (token, u["id"], db.now(), _expiry()))
    db.audit(con, u["username"], "login", {"role": u["role"]})
    con.commit(); con.close()
    return {"token": token, "role": u["role"], "username": u["username"]}


@router.post("/api/logout")
def logout(user: dict = Depends(get_user)):
    con = db.connect()
    con.execute("DELETE FROM sessions WHERE token = ?", (user["token"],))
    db.audit(con, user["username"], "logout", {})
    con.commit(); con.close()
    return {"ok": True}
