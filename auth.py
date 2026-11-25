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
import struct
import time
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


# ---------------------------------------------------------------------------
# Two-factor authentication
# ---------------------------------------------------------------------------
#
# Two methods, and the second one is optional per deployment:
#
#   * **An authenticator app** (TOTP, RFC 6238). Works offline, needs no
#     vendor and no outbound connection, and is what a security reviewer
#     expects to see.
#   * **A code emailed to the account's address**, allowed only when the
#     `allow_email_2fa` preference is on AND mail actually works. Some
#     organizations will refuse this and they are right to think about it: an
#     attacker who controls the mailbox gets the code *and* the password reset
#     link, so email is a second factor against password reuse and credential
#     stuffing, not against a compromised inbox. The preference exists so that
#     judgment belongs to the deployment rather than to us.
#
# **Nothing here touches the database.** Callers own persistence, like the rest
# of this module.

TOTP_DIGITS = 6
TOTP_PERIOD = 30
# SHA-1 on purpose, not by oversight: it is what every authenticator app
# assumes when an otpauth:// URI omits the algorithm, and HMAC-SHA1 is
# untouched by the collision attacks that ended SHA-1 for signatures. The
# functions take the algorithm as an argument, so this is one line to change if
# that ever stops being true.
TOTP_ALGO = "sha1"
# One step either side, so a phone whose clock is up to thirty seconds out in
# either direction still works. Wider would start to matter: each extra step
# is another code an attacker gets to guess against.
TOTP_WINDOW = 1

TOTP_SECRET_BYTES = 20          # 160 bits, per RFC 4226's recommendation
TOTP_SALT_BYTES = 16

TWOFA_KEY_ENV = "TWOFA_SECRET_KEY"

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 5         # 8 base32 characters, shown in two groups

EMAIL_CODE_DIGITS = 6
EMAIL_CODE_MINUTES = 10
# A six-digit emailed code is only 10^6 wide, so unlike a TOTP code — which an
# attacker gets a bounded number of guesses at per 30-second step — this one
# sits still until it expires. The attempt cap is what makes it safe, and it is
# the challenge that is spent, not merely the code: five wrong answers and the
# whole challenge is dead and a fresh sign-in is needed.
CHALLENGE_MAX_ATTEMPTS = 5
CHALLENGE_MINUTES = 10

TRUSTED_DEVICE_DAYS = 30
TRUSTED_DEVICE_COOKIE = "dlt_device"


def twofa_key():
    """The server key that every TOTP secret is derived from, or None.

    Returns the raw bytes, or None when the environment variable is unset or
    too short to be worth using.
    """
    raw = (os.environ.get(TWOFA_KEY_ENV) or "").strip()
    if len(raw) < 32:
        return None
    return raw.encode("utf-8")


def twofa_key_problem():
    """A sentence naming what is wrong with the key, or None when it is fine.

    Read by Preferences so an administrator is told why enrollment is refused,
    rather than finding a control that does not work.
    """
    raw = (os.environ.get(TWOFA_KEY_ENV) or "").strip()
    if not raw:
        return (f"{TWOFA_KEY_ENV} is not set, so authenticator-app enrollment "
                f"is unavailable. Generate one with "
                f"`python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"` "
                f"and set it in the environment.")
    if len(raw) < 32:
        return (f"{TWOFA_KEY_ENV} is set but is shorter than 32 characters. "
                f"Replace it with at least 32 characters of random text.")
    return None


def derive_totp_secret(user_id, salt_b64, key=None):
    """Derive a user's TOTP secret. Nothing secret is ever stored.

    The database holds only ``salt`` — random, per enrollment, and **not**
    secret. The secret itself is recomputed on demand from the server key:

        secret = HMAC-SHA256(server_key, b"totp" || user_id || salt)[:20]

    **Why derive rather than encrypt.** A TOTP secret is symmetric, so unlike a
    password it cannot be hashed — the server needs the same value the phone
    has. The obvious answer is to encrypt the column, and that was the first
    plan. But Python's standard library has no symmetric cipher, so encrypting
    means either adding a large compiled dependency or composing a cipher out
    of HMAC by hand, which is bespoke cryptography in a project whose security
    document is read by people whose job is to distrust exactly that.

    Deriving needs only `hmac`, which this module already imports, and gives a
    stronger property than encryption: **a stolen database or backup contains
    no secret at all**, not even a ciphertext. There is nothing to decrypt.

    The costs are the ones encryption would also have had. The key must exist
    wherever the app runs; losing it means every enrollment is void and
    everybody re-enrolls; rotating it means the same. The salt is what lets one
    user re-enroll and get a genuinely new secret without touching anybody
    else's — and it is stored in the clear because it is not a secret, exactly
    like a password salt.

    The domain-separation prefix (``b"totp"``) means this key can be reused for
    another purpose later without the two deriving the same bytes.
    """
    key = key or twofa_key()
    if not key:
        raise ValueError(f"{TWOFA_KEY_ENV} is not set or is too short")
    material = b"totp" + str(user_id).encode("ascii") + b"|" + salt_b64.encode("ascii")
    return hmac.new(key, material, hashlib.sha256).digest()[:TOTP_SECRET_BYTES]


def new_totp_salt():
    """A fresh, non-secret salt. Changing it re-enrolls that one user."""
    return _b64e(secrets.token_bytes(TOTP_SALT_BYTES))


def totp_secret_b32(secret: bytes) -> str:
    """Base32, which is what authenticator apps and otpauth:// URIs speak."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def totp_secret_groups(secret: bytes) -> str:
    """The secret in groups of four, for somebody typing it off a screen."""
    text = totp_secret_b32(secret)
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))


def hotp(secret: bytes, counter: int, digits=TOTP_DIGITS, algo=TOTP_ALGO) -> str:
    """RFC 4226 HOTP. The dynamic truncation is the fiddly part of the spec."""
    mac = hmac.new(secret, struct.pack(">Q", counter), algo).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def totp_code(secret: bytes, at=None, period=TOTP_PERIOD, digits=TOTP_DIGITS,
              algo=TOTP_ALGO) -> str:
    """RFC 6238 TOTP: HOTP with the counter being the current time step."""
    moment = int(at if at is not None else time.time())
    return hotp(secret, moment // period, digits=digits, algo=algo)


def verify_totp(secret: bytes, code, at=None, window=TOTP_WINDOW,
                period=TOTP_PERIOD, digits=TOTP_DIGITS, algo=TOTP_ALGO) -> bool:
    """Check a code against a small window of time steps.

    **Every candidate in the window is compared, with no early return**, so the
    time this takes does not reveal which step matched — and each comparison is
    `hmac.compare_digest`, so it does not reveal how much of the code was
    right either.

    Codes are normalized first because people paste them with the space the
    authenticator app displays.
    """
    code = re.sub(r"\s+", "", str(code or ""))
    if not code.isdigit() or len(code) != digits:
        return False
    moment = int(at if at is not None else time.time())
    step = moment // period
    ok = False
    for drift in range(-window, window + 1):
        ok |= hmac.compare_digest(hotp(secret, step + drift, digits, algo), code)
    return bool(ok)


def totp_uri(secret: bytes, email, issuer):
    """The otpauth:// URI an authenticator app understands.

    Worth knowing: on a phone this is also a **tappable link** that opens the
    authenticator and configures it, which is why enrollment offers it beside
    the QR code. The QR is only strictly needed when the page is on a desktop
    and the app is on a separate phone — which is, admittedly, most people.

    The issuer appears twice by convention: once as a prefix on the label and
    once as a parameter. Apps that only read one still show something sensible.
    """
    from urllib.parse import quote
    label = quote(f"{issuer}:{email}", safe="")
    params = (f"secret={totp_secret_b32(secret)}"
              f"&issuer={quote(issuer, safe='')}"
              f"&algorithm={TOTP_ALGO.upper()}"
              f"&digits={TOTP_DIGITS}&period={TOTP_PERIOD}")
    return f"otpauth://totp/{label}?{params}"


# --- Recovery codes --------------------------------------------------------

def new_recovery_codes(count=RECOVERY_CODE_COUNT):
    """Fresh single-use codes, returned in the clear for one-time display.

    Crockford-ish base32 without padding: no lowercase to mistype, and the
    alphabet avoids nothing in particular — but the codes are shown in two
    groups of four, which is what makes them transcribable at all.
    """
    out = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(RECOVERY_CODE_BYTES))
        text = raw.decode("ascii").rstrip("=")
        out.append(f"{text[:4]}-{text[4:]}")
    return out


def normalize_recovery_code(code):
    """Upper-cased, stripped of spaces and dashes, so typing style never matters."""
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def hash_recovery_code(code):
    """SHA-256, like a session token and for the same reason.

    These are 40 bits of random output, not a password, so there is nothing for
    a slow hash to protect against — and one of these is checked on a path
    where somebody is already locked out and anxious.
    """
    return hashlib.sha256(normalize_recovery_code(code).encode("ascii")).hexdigest()


# --- Emailed codes, challenges and trusted devices -------------------------

def new_email_code(digits=EMAIL_CODE_DIGITS):
    """A numeric code for an emailed challenge.

    `randbelow` rather than `randint`: this needs to be unguessable, and the
    zero padding matters so every code is the same length on screen.
    """
    return str(secrets.randbelow(10 ** digits)).zfill(digits)


def hash_code(code):
    """Hash an emailed code before storing the challenge.

    The challenge row would otherwise hold a live credential, and the whole
    point of hashing the reset token was that rows like this end up in
    backups.
    """
    return hashlib.sha256(re.sub(r"\s+", "", str(code or "")).encode("utf-8")).hexdigest()


def new_challenge_token():
    """Identifies a half-finished sign-in. Not a session: it grants nothing."""
    return secrets.token_urlsafe(32)


def new_device_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    """Shared hashing for challenge and device tokens, as for sessions."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()
