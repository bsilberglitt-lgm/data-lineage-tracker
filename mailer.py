"""Sending email, over SMTP or Resend's HTTP API.

This exists so an organization adopting the tracker can point it at their own
mail infrastructure without touching the code. Two transports, because the two
kinds of adopter are genuinely different:

* **SMTP** is the lingua franca. Most state agencies already run a relay and
  will not want an external vendor in the path for a password reset. It is
  also what Resend, SendGrid, Microsoft 365 and Google Workspace all speak, so
  one code path covers almost everybody.
* **Resend's HTTP API** is for anyone who would rather not open an outbound
  SMTP port, which some hosting platforms make awkward.

**Settings live in the `preference` table; the secret does not.** The host,
port, from-address and so on are edited by a SuperAdmin in Preferences, so
setting this up does not require infrastructure access. The password or API key
comes from the environment — either a variable or, better, the path of a file
holding it; see `read_secret`. The reason it is not in the database is the
backups: a credential there travels in all 45 retained nightly snapshots, and
encrypting it would need a key that must itself come from the environment, so
it would move the secret rather than protect it and add a rotation problem on
the way.

**On Microsoft 365, do not use this with a username and password.** Exchange
Online began disabling Basic Authentication for SMTP AUTH by default in
December 2026, with permanent removal to follow, and this module authenticates
with a password only — no XOAUTH2, no Graph. That is a deliberate scope limit
rather than an oversight, because the destinations an adopter should actually
use all speak ordinary SMTP: AWS **SES** has an SMTP interface with its own
credentials, Azure **Communication Services Email** authenticates at
`smtp.azurecomm.net:587` with an Entra application secret as the password, and
an internal agency relay usually wants no authentication at all (`security`
`none`, no username). If a deployment genuinely must submit through Exchange
Online, that needs OAuth support adding first — see `help_articles.py` and the
install documentation for the user-facing version of this.

**Nothing here raises for a delivery failure.** `send` returns
`(ok, detail)`, because every caller has to carry on either way: a password
reset that cannot be mailed must still answer the browser without saying
whether the address exists, and the test button needs the real reason to show
the administrator.

**Nothing here logs a message body.** A reset email contains a working token
for somebody's account, so a log line carrying one is a credential at rest in
whatever aggregates the logs. Addresses and subjects are fine; bodies are not.

Standard library only. `urllib` for Resend rather than `requests`, so adopting
this adds no dependency to install, pin or audit.
"""

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

# What `transport` may be set to. Checked on write the way LOGO_POSITIONS is,
# so a junk value cannot reach the point of sending.
TRANSPORTS = ("none", "smtp", "resend")

# How SMTP reaches the server. Three real choices and they are not
# interchangeable:
#   starttls — connect in the clear on 587, then upgrade. What most relays want.
#   ssl      — TLS from the first byte, on 465.
#   none     — no encryption at all. Only defensible for a relay on localhost
#              or inside a trusted network, and named explicitly so nobody
#              arrives at it by leaving a field blank.
SMTP_SECURITY = ("starttls", "ssl", "none")

RESEND_ENDPOINT = "https://api.resend.com/emails"

# Where the secret comes from, per transport. Two forms, and the file form is
# not a convenience — it is how the platforms this is likely to run on actually
# deliver a secret to a container. AWS ECS task secrets, Azure Key Vault
# through the CSI driver, and Docker secrets all mount the value as a **file**.
# An app that can only read an environment variable therefore forces an adopter
# to copy the credential out of their secret store and into their deployment
# configuration by hand, which is the specific thing their security team is
# trying to prevent.
#
# It also buys rotation: the file is read **per send** rather than cached, so
# replacing the mounted secret takes effect with no restart. One small read per
# message is nothing beside an SMTP round trip.
SECRET_ENV = {
    "smtp": "SMTP_PASSWORD",
    "resend": "RESEND_API_KEY",
}

SECRET_FILE_ENV = {
    "smtp": "SMTP_PASSWORD_FILE",
    "resend": "RESEND_API_KEY_FILE",
}

# Test-only. When set, nothing is sent: each message is appended to this file as
# one JSON object per line, and `send` reports success. It exists because the
# feature suites run against a real server process, so an in-memory list would
# be invisible to them, and because a test suite must never be able to mail a
# real person.
#
# **It takes two variables, and that is the point.** A single obscure variable
# that makes password-reset mail report success and deliver nothing is a hole
# however loudly it is announced: an environment file copied from a test
# deployment carries it along, and the symptom is users who never receive a
# reset and an administrator with no reason to suspect the mail settings they
# can see are correct. So the path alone is **refused at startup**
# (`sink_refusal`) and the sink is only active when `EMAIL_SINK_CONFIRM` is
# also set to exactly `SINK_CONFIRM_VALUE`.
#
# The asymmetry is deliberate: one variable is a mistake and crashes, two are
# a decision and work. A crash on the way up is the cheapest possible place to
# find this, because nothing has silently swallowed a message yet.
SINK_ENV = "EMAIL_SINK_PATH"
SINK_CONFIRM_ENV = "EMAIL_SINK_CONFIRM"
SINK_CONFIRM_VALUE = "discard-all-mail"

TIMEOUT_SECONDS = 20


def sink_configured():
    """Whether a sink path is set at all, confirmed or not."""
    return bool((os.environ.get(SINK_ENV) or "").strip())


def sink_confirmed():
    return ((os.environ.get(SINK_CONFIRM_ENV) or "").strip()
            == SINK_CONFIRM_VALUE)


def sink_path():
    """The sink file, or None. Requires BOTH variables — see the note above."""
    if not sink_confirmed():
        return None
    return (os.environ.get(SINK_ENV) or "").strip() or None


def sink_refusal():
    """Why the app must not start, or None if there is no reason.

    Returned rather than raised so the caller decides what to do with it, and
    so a test can assert the sentence without arranging to catch an exit.
    """
    if sink_configured() and not sink_confirmed():
        return (f"{SINK_ENV} is set. That writes every message to a file and "
                f"reports it as sent, so a password reset would succeed and "
                f"deliver nothing. It is a test arrangement and must not be "
                f"reachable by accident, so it also requires "
                f"{SINK_CONFIRM_ENV}={SINK_CONFIRM_VALUE}. Set that as well "
                f"if this really is a test environment, or unset {SINK_ENV}.")
    return None


def read_secret(transport):
    """`(value, source, error)` for this transport's credential.

    `source` is "file", "env" or None, so an administrator can be told where
    the credential was found without ever being shown it.

    **A `*_FILE` that is set but unreadable is an error, not a reason to fall
    back to the plain variable.** Falling back would mean a deployment whose
    secret mount had failed quietly authenticating with whatever stale value
    happened to be left in the environment — something wrong looking like
    something working, which is the one failure shape this project keeps
    paying for.

    Returns "" rather than None for the value, so a caller cannot accidentally
    send the string "None" as a password.
    """
    file_name = SECRET_FILE_ENV.get(transport)
    path = (os.environ.get(file_name) or "").strip() if file_name else ""
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                # Stripped because a mounted secret almost always ends in a
                # newline, and a trailing newline in a password is an
                # authentication failure nobody can see by looking.
                return fh.read().strip(), "file", None
        except OSError as e:
            return "", None, (f"{file_name} is set to {path}, which cannot be "
                              f"read ({e.strerror or e}).")
    name = SECRET_ENV.get(transport)
    value = (os.environ.get(name) or "").strip() if name else ""
    return value, ("env" if value else None), None


def secret_for(transport):
    """Just the credential, for callers that do not care where it came from."""
    return read_secret(transport)[0]


def config_from_preferences(prefs):
    """The settings needed to send, read out of the preference values.

    Takes the plain {key: value} mapping rather than the database, so this
    module needs no connection and the test suite can build a config by hand.
    """
    transport = (prefs.get("email_transport") or "none").strip().lower()
    if transport not in TRANSPORTS:
        transport = "none"
    security = (prefs.get("smtp_security") or "starttls").strip().lower()
    if security not in SMTP_SECURITY:
        security = "starttls"
    try:
        port = int(str(prefs.get("smtp_port") or "587").strip())
    except ValueError:
        port = 587
    secret, secret_source, secret_error = read_secret(transport)
    return {
        "transport": transport,
        "host": (prefs.get("smtp_host") or "").strip(),
        "port": port,
        "security": security,
        "username": (prefs.get("smtp_username") or "").strip(),
        "from_address": (prefs.get("email_from_address") or "").strip(),
        "from_name": (prefs.get("email_from_name") or "").strip(),
        "secret": secret,
        # Where it came from and why it could not be read. Both are about the
        # credential without being the credential, so both are safe to report.
        "secret_source": secret_source,
        "secret_error": secret_error,
    }


def describe(cfg):
    """What an administrator needs to see, with no credential in it.

    `secret_present` rather than the secret: the Preferences screen has to be
    able to say "we found the API key" without the key crossing the wire, and a
    settings endpoint that returns a credential is one copy-paste away from a
    support ticket.
    """
    return {
        "transport": cfg["transport"],
        "configured": is_configured(cfg),
        "secret_env_var": SECRET_ENV.get(cfg["transport"]),
        "secret_file_env_var": SECRET_FILE_ENV.get(cfg["transport"]),
        "secret_present": bool(cfg["secret"]),
        # "file" or "env", so an administrator can tell a mounted secret from
        # a stale environment variable without either value crossing the wire.
        "secret_source": cfg.get("secret_source"),
        "problems": problems(cfg),
        "sink_active": bool(sink_path()),
    }


def problems(cfg):
    """Every reason this config cannot send, as sentences for a person.

    All of them at once rather than the first: an administrator filling in a
    new screen would otherwise fix one field, save, and be told about the next.
    """
    out = []
    if cfg["transport"] == "none":
        return ["No email transport is selected."]
    if not cfg["from_address"]:
        out.append("A from address is required.")
    elif "@" not in cfg["from_address"]:
        out.append("The from address does not look like an email address.")
    env_name = SECRET_ENV.get(cfg["transport"])
    file_name = SECRET_FILE_ENV.get(cfg["transport"])
    if cfg.get("secret_error"):
        # Reported instead of the generic "no credential", and deliberately
        # distinct from it: a mount that failed and a secret nobody set need
        # different things done about them.
        out.append(cfg["secret_error"])
    elif not cfg["secret"]:
        out.append(f"No credential found. Set {env_name} in the environment "
                   f"where the app runs, or {file_name} to the path of a file "
                   f"holding it — which is how AWS, Azure and Docker mount a "
                   f"secret — then restart it.")
    if cfg["transport"] == "smtp":
        if not cfg["host"]:
            out.append("An SMTP host is required.")
        if not 1 <= cfg["port"] <= 65535:
            out.append("The SMTP port must be between 1 and 65535.")
        if cfg["secret"] and not cfg["username"]:
            out.append("An SMTP username is required when a password is set.")
    return out


def is_configured(cfg):
    return not problems(cfg)


def build_message(cfg, to_address, subject, text_body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (f'{cfg["from_name"]} <{cfg["from_address"]}>'
                   if cfg["from_name"] else cfg["from_address"])
    msg["To"] = to_address
    # Plain text only, deliberately. Every message this app sends is a sentence
    # and a link; an HTML part would double what has to be reviewed for every
    # future message and is what makes mail look like phishing to a filter.
    msg.set_content(text_body)
    return msg


def send(cfg, to_address, subject, text_body):
    """Send one message. Returns (ok, detail) and never raises.

    `detail` is written to be shown to an administrator: it says what failed
    and, where the answer is knowable, what to do about it.
    """
    if not to_address or "@" not in to_address:
        return False, "No valid recipient address."
    bad = problems(cfg)
    if bad:
        return False, " ".join(bad)

    path = sink_path()
    if path:
        return _send_to_sink(path, cfg, to_address, subject, text_body)
    if cfg["transport"] == "smtp":
        return _send_smtp(cfg, to_address, subject, text_body)
    if cfg["transport"] == "resend":
        return _send_resend(cfg, to_address, subject, text_body)
    return False, f"Unknown transport '{cfg['transport']}'."


def _send_to_sink(path, cfg, to_address, subject, text_body):
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "to": to_address,
                "subject": subject,
                "body": text_body,
                "from": cfg["from_address"],
                "transport": cfg["transport"],
            }) + "\n")
        return True, f"Written to the test sink at {path}."
    except OSError as e:
        return False, f"Could not write to the test sink: {e}"


def _send_smtp(cfg, to_address, subject, text_body):
    msg = build_message(cfg, to_address, subject, text_body)
    try:
        if cfg["security"] == "ssl":
            # `create_default_context` verifies the certificate and the
            # hostname. Both matter: without them this would hand the
            # credential to anything that answered on the port.
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                                      timeout=TIMEOUT_SECONDS,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"],
                                  timeout=TIMEOUT_SECONDS)
        with server:
            if cfg["security"] == "starttls":
                server.starttls(context=ssl.create_default_context())
            if cfg["username"]:
                server.login(cfg["username"], cfg["secret"])
            server.send_message(msg)
        return True, "Sent."
    except smtplib.SMTPAuthenticationError:
        # Deliberately not echoing the server's text here: some relays include
        # the username, and this string is shown in a browser.
        return False, ("The mail server rejected the username and password. "
                       f"Check the username and the {SECRET_ENV['smtp']} "
                       "environment variable.")
    except smtplib.SMTPRecipientsRefused:
        return False, "The mail server refused the recipient address."
    except smtplib.SMTPSenderRefused:
        return False, ("The mail server refused the from address. Most relays "
                       "only accept a from address on a domain they handle.")
    except smtplib.SMTPNotSupportedError as e:
        return False, (f"The mail server does not support what was asked of "
                       f"it ({e}). Check the encryption setting.")
    except ssl.SSLError as e:
        return False, (f"The TLS handshake failed ({e}). A relay on port 587 "
                       "usually wants STARTTLS; port 465 usually wants SSL.")
    except smtplib.SMTPException as e:
        return False, f"The mail server returned an error: {e}"
    except OSError as e:
        # Covers a wrong host, a closed port and a timeout, which is most of
        # what actually goes wrong on a first attempt.
        return False, (f"Could not reach {cfg['host']} on port {cfg['port']} "
                       f"({e}).")


def _send_resend(cfg, to_address, subject, text_body):
    payload = json.dumps({
        "from": (f'{cfg["from_name"]} <{cfg["from_address"]}>'
                 if cfg["from_name"] else cfg["from_address"]),
        "to": [to_address],
        "subject": subject,
        "text": text_body,
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_ENDPOINT, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['secret']}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            if 200 <= resp.status < 300:
                return True, "Sent."
            return False, f"Resend returned HTTP {resp.status}."
    except urllib.error.HTTPError as e:
        # Resend puts a usable sentence in the body, which is worth passing on
        # — an unverified sending domain is the single most common failure and
        # the message says so plainly. Truncated, because it reaches a browser.
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        if e.code in (401, 403):
            return False, (f"Resend rejected the API key. Check the "
                           f"{SECRET_ENV['resend']} environment variable. {body}")
        return False, f"Resend returned HTTP {e.code}. {body}"
    except urllib.error.URLError as e:
        return False, f"Could not reach Resend ({e.reason})."
    except OSError as e:
        return False, f"Could not reach Resend ({e})."
