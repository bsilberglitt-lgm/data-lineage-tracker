"""Authentication primitives: password hashing, password policy, sessions.

Design notes
------------
* **Hashing uses stdlib ``hashlib.scrypt``** rather than Argon2. scrypt is
  memory-hard and is an accepted modern choice (OWASP lists it alongside
  Argon2id), and using the standard library means no extra dependency and no
  native build step — which keeps the container small and the app portable to
  restricted environments.
* **Password checks are constant-time** via ``hmac.compare_digest``.
* **Session tokens are stored hashed.** The raw token only ever exists in the
  user's cookie; the database holds a SHA-256 of it, so a database leak does not
  hand over live sessions.
* Nothing here talks to the database — callers own persistence. That keeps this
  module easy to test and easy to swap when SSO (Option C) replaces local
  passwords later.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import unicodedata

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

# scrypt cost parameters. n=2**15 with r=8 needs roughly 32 MiB per hash, which
# is a deliberate balance: strong against offline cracking while staying safe on
# a small (512 MB) instance handling occasional logins. Raise SCRYPT_N on
# larger hardware — existing hashes keep working because the parameters are
# stored alongside each hash.
SCRYPT_N = int(os.environ.get("SCRYPT_N", 2 ** 15))
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str) -> str:
    """Return an encoded hash: ``scrypt$n$r$p$salt$hash``."""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_N * SCRYPT_R * 256,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against an encoded hash, in constant time."""
    if not encoded or not password:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=n, r=r, p=p, dklen=len(expected),
            maxmem=n * r * 256,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

# A deliberately small, bundled list of the passwords attackers try first.
# Screening locally means no external API call on the login path and no
# dependency on outbound network access (which matters in restricted government
# networks). A Have I Been Pwned lookup can be added later behind a preference.
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "p@ssw0rd", "p@ssword",
    "123456", "1234567", "12345678", "123456789", "1234567890", "12345",
    "qwerty", "qwerty123", "qwertyuiop", "letmein", "welcome", "welcome1",
    "admin", "admin123", "administrator", "root", "toor", "guest", "test",
    "iloveyou", "monkey", "dragon", "sunshine", "princess", "football",
    "baseball", "master", "shadow", "superman", "batman", "trustno1",
    "abc123", "abcd1234", "a1b2c3", "111111", "000000", "121212", "654321",
    "changeme", "changeme1", "default", "secret", "login", "pass", "temp",
    "temppassword", "newpassword", "mypassword", "school", "student",
    "teacher", "education", "maine", "lineage", "mainelineage", "data",
    "database", "summer", "winter", "spring", "autumn", "january", "december",
}

MIN_LENGTH_DEFAULT = 12


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def check_password_policy(password, policy=None, email=None, first_name=None,
                          last_name=None):
    """Validate a password. Returns a list of human-readable problems (empty = OK).

    ``policy`` is a dict of preference values so a SuperAdmin can tune the rules
    without a code change.
    """
    policy = policy or {}
    min_len = int(policy.get("password_min_length", MIN_LENGTH_DEFAULT))
    need_upper = _truthy(policy.get("password_require_uppercase", True))
    need_lower = _truthy(policy.get("password_require_lowercase", True))
    need_number = _truthy(policy.get("password_require_number", True))
    need_symbol = _truthy(policy.get("password_require_symbol", True))

    problems = []
    if password is None:
        return ["Password is required."]

    # Normalize so visually identical characters compare consistently.
    pw = unicodedata.normalize("NFKC", password)

    if len(pw) < min_len:
        problems.append(f"Must be at least {min_len} characters long.")
    if need_upper and not re.search(r"[A-Z]", pw):
        problems.append("Must include an uppercase letter.")
    if need_lower and not re.search(r"[a-z]", pw):
        problems.append("Must include a lowercase letter.")
    if need_number and not re.search(r"[0-9]", pw):
        problems.append("Must include a number.")
    if need_symbol and not re.search(r"[^A-Za-z0-9]", pw):
        problems.append("Must include a symbol.")

    lowered = pw.lower()
    if lowered in COMMON_PASSWORDS:
        problems.append("This is a commonly used password. Choose something less predictable.")
    else:
        # Also catch "Password123!" style variations of a common base word.
        stripped = re.sub(r"[^a-z]", "", lowered)
        if stripped and stripped in COMMON_PASSWORDS and len(stripped) >= 5:
            problems.append("This is too close to a commonly used password.")

    if len(set(pw)) < 5:
        problems.append("Uses too few distinct characters.")
    if re.search(r"(.)\1{3,}", pw):
        problems.append("Avoid repeating the same character four or more times.")

    # Don't let people use their own name or email as the password.
    for personal in (email or "").split("@")[0], first_name, last_name:
        if personal and len(personal) >= 3 and personal.lower() in lowered:
            problems.append("Must not contain your name or email address.")
            break

    return problems


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def describe_password_policy(policy=None):
    """Human-readable policy summary, shown on the login/change-password screens."""
    policy = policy or {}
    min_len = int(policy.get("password_min_length", MIN_LENGTH_DEFAULT))
    parts = [f"at least {min_len} characters"]
    if _truthy(policy.get("password_require_uppercase", True)):
        parts.append("an uppercase letter")
    if _truthy(policy.get("password_require_lowercase", True)):
        parts.append("a lowercase letter")
    if _truthy(policy.get("password_require_number", True)):
        parts.append("a number")
    if _truthy(policy.get("password_require_symbol", True)):
        parts.append("a symbol")
    return "Passwords need " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Email domain allow-list
# ---------------------------------------------------------------------------

def email_domain_allowed(email, allowed_domains):
    """Check an email against the configured allow-list.

    ``allowed_domains`` is a comma-separated string. Empty, ``*`` or ``all``
    means every domain is permitted.
    """
    email = normalize_email(email)
    if "@" not in email:
        return False, "Enter a valid email address."
    raw = (allowed_domains or "").strip()
    if raw == "" or raw in ("*", "all", "ALL"):
        return True, None
    domain = email.rsplit("@", 1)[1]
    allowed = [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]
    if domain in allowed:
        return True, None
    return False, f"Email domain '{domain}' is not allowed. Permitted: {', '.join(allowed)}."


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SESSION_COOKIE = "dlt_session"
CSRF_HEADER = "X-CSRF-Token"
IDLE_TIMEOUT_HOURS_DEFAULT = 8
# Even an actively used session is re-authenticated after this long.
ABSOLUTE_SESSION_DAYS = 30


def new_session_token():
    """Return (raw_token, token_hash). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_session_token(raw)


def hash_session_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_csrf_token():
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------------

# How long a reset link works for. Short enough that a link left in an inbox
# stops being a key fairly quickly, long enough to survive somebody asking for
# it, going to a meeting, and coming back.
RESET_TOKEN_MINUTES = 60

# How many links one account may ask for in RESET_RATE_WINDOW_MINUTES.
# Counted per **account**, not per IP: the thing being prevented is somebody
# else's inbox being flooded, and an attacker doing that will not keep the same
# address. A legitimate person asking twice because the first mail was slow
# must not be stopped, which is why this is not 1.
RESET_RATE_LIMIT = 5
RESET_RATE_WINDOW_MINUTES = 60


def new_reset_token():
    """Return (raw_token, token_hash). Only the hash is ever stored.

    Same shape as `new_session_token`, and the same reasoning: the raw value is
    a bearer credential, so the database holds only enough to recognize one
    that is presented. SHA-256 rather than scrypt because 32 bytes of
    `token_urlsafe` has nothing to brute-force — the slow hash a password needs
    would buy nothing here and cost a comparison on every click.
    """
    raw = secrets.token_urlsafe(32)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw):
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def reset_link(base_url, raw_token):
    """The URL that goes in the email.

    `base_url` comes from the `app_base_url` setting and **never** from the
    request's Host header. Building it from the header is the standard way
    reset links get poisoned: an attacker sends a forgot-password request for
    somebody else's address with a Host of their own choosing, and the victim
    receives a real token pointing at the attacker's server. Reading it from
    configuration means a forged request cannot change where the link goes.

    **The token is in the fragment, not the query string**, and that is a
    deliberate change from the first version. A fragment is never transmitted
    to the server: a browser strips everything from the `#` onwards before
    sending the request. A query string is not, so `?token=...` is written into
    the access log of every reverse proxy, load balancer and CDN in the path —
    Render's today, an ALB or Application Gateway later — where it sits as a
    live credential in whatever aggregates those logs, readable by anyone with
    log access and retained on whatever schedule they are retained on. Clearing
    it from the address bar with `history.replaceState`, which the browser also
    does, protects browser history and does nothing whatever about that.

    Nothing server-side reads it from the URL either way: the page is served
    for the path alone and the browser POSTs the token it found. So this costs
    nothing and closes the log exposure completely.
    """
    return f"{(base_url or '').rstrip('/')}/reset-password#token={raw_token}"


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------

LOCKOUT_THRESHOLD = 10          # failed attempts before a temporary lock
LOCKOUT_MINUTES = 15            # how long the lock lasts
PROGRESSIVE_DELAY_AFTER = 3     # start slowing down after this many failures
PROGRESSIVE_DELAY_MAX = 5.0     # seconds


def login_delay_seconds(recent_failures):
    """Progressive backoff: slow guessing down without locking anyone out early."""
    if recent_failures < PROGRESSIVE_DELAY_AFTER:
        return 0.0
    over = recent_failures - PROGRESSIVE_DELAY_AFTER + 1
    return min(PROGRESSIVE_DELAY_MAX, 0.5 * (2 ** (over - 1)))
