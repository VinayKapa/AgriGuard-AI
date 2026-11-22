from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.database import connect

router = APIRouter(prefix="/api/auth", tags=["authentication"])
SESSION_DAYS = 14
SESSION_COOKIE = "agriguard_session"


def _cookie_name():
    return "__Host-agriguard_session" if settings.app_env.lower() in {"production", "prod"} else SESSION_COOKIE


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=80)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue(user_id: int) -> str:
    token = secrets.token_urlsafe(36)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with connect() as db:
        db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (_token_hash(token), user_id, expires))
    return token


def _set_session(response: Response, token: str):
    response.set_cookie(
        key=_cookie_name(), value=token, max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True, secure=settings.app_env.lower() in {"production", "prod"},
        samesite="lax", path="/",
    )


def _clear_session(response: Response):
    response.delete_cookie(
        key=_cookie_name(), httponly=True,
        secure=settings.app_env.lower() in {"production", "prod"},
        samesite="lax", path="/",
    )


def _user(row):
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def _rate_limit(request: Request, email: str):
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=15)).isoformat()
    ip = (request.client.host if request.client else "unknown")[:80]
    # Store only hashes of the submitted identifiers; never persist raw IP addresses here.
    keys = [hashlib.sha256(f"email:{email.strip().lower()}".encode()).hexdigest(), hashlib.sha256(f"ip:{ip}".encode()).hexdigest()]
    with connect() as db:
        db.execute("DELETE FROM auth_attempts WHERE attempted_at < ?", ((now - timedelta(days=1)).isoformat(),))
        counts = [db.execute("SELECT COUNT(*) FROM auth_attempts WHERE bucket=? AND attempted_at>=?", (key, cutoff)).fetchone()[0] for key in keys]
        if counts[0] >= 12 or counts[1] >= 60:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Please wait 15 minutes and try again.")
        db.executemany("INSERT INTO auth_attempts(bucket,attempted_at) VALUES(?,?)", [(key, now.isoformat()) for key in keys])


@router.post("/register")
def register(body: Registration, request: Request, response: Response):
    email = body.email.strip().lower()
    _rate_limit(request, email)
    salt = secrets.token_bytes(16)
    try:
        with connect() as db:
            values = (body.name.strip(), email, _hash(body.password, salt), salt)
            if settings.database_url:
                user_id = db.execute("INSERT INTO users(name,email,password_hash,password_salt) VALUES(?,?,?,?) RETURNING id", values).fetchone()["id"]
            else:
                user_id = db.execute("INSERT INTO users(name,email,password_hash,password_salt) VALUES(?,?,?,?)", values).lastrowid
    except Exception as exc:
        if isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23505":
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise
    _set_session(response, _issue(user_id))
    return {"user": {"id": user_id, "name": body.name.strip(), "email": email}}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    email = body.email.strip().lower()
    _rate_limit(request, email)
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not secrets.compare_digest(_hash(body.password, row["password_salt"]), row["password_hash"]):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    _set_session(response, _issue(row["id"]))
    return {"user": _user(row)}


def current_user(request: Request):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get(_cookie_name())
    if not token:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    with connect() as db:
        row = db.execute("SELECT users.*, sessions.token_hash FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?", (_token_hash(token), datetime.now(timezone.utc).isoformat())).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Your session expired. Please sign in again.")
    return _user(row)


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.post("/logout")
def logout(request: Request, response: Response):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get(_cookie_name())
    if token:
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    _clear_session(response)
    return {"ok": True}
