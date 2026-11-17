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
comes from an environment variable and nothing else — see `secret_for`. The
reason is the backups: a credential in the database travels in all 45 retained
nightly snapshots, and encrypting it there would need a key that must itself be
an environment variable, so it would move the secret rather than protect it and
add a rotation problem on the way.

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

# Where the secret comes from, per transport. The only two environment
# variables this module reads for credentials.
SECRET_ENV = {
    "smtp": "SMTP_PASSWORD",
    "resend": "RESEND_API_KEY",
}

# Test-only. When set, nothing is sent: each message is appended to this file as
# one JSON object per line, and `send` reports success. It exists because the
# feature suites run against a real server process, so an in-memory list would
# be invisible to them, and because a test suite must never be able to mail a
# real person.
#
# **This is a hole if it is ever set in production**, so the app logs a loud
# warning at startup when it finds it, and the name is deliberately not
# something anybody would set by accident.
SINK_ENV = "EMAIL_SINK_PATH"

TIMEOUT_SECONDS = 20


def sink_path():
    return (os.environ.get(SINK_ENV) or "").strip() or None


def secret_for(transport):
    """The credential for this transport, from the environment only.

    Returns "" rather than None when unset, so a caller cannot accidentally
    send the string "None" as a password.
    """
    name = SECRET_ENV.get(transport)
    return (os.environ.get(name) or "").strip() if name else ""


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
    return {
        "transport": transport,
        "host": (prefs.get("smtp_host") or "").strip(),
        "port": port,
        "security": security,
        "username": (prefs.get("smtp_username") or "").strip(),
        "from_address": (prefs.get("email_from_address") or "").strip(),
        "from_name": (prefs.get("email_from_name") or "").strip(),
        "secret": secret_for(transport),
    }


def describe(cfg):
    """What an administrator needs to see, with no credential in it.

    `secret_present` rather than the secret: the Preferences screen has to be
    able to say "we found the API key" without the key crossing the wire, and a
    settings endpoint that returns a credential is one copy-paste away from a
    support ticket.
    """
    env_name = SECRET_ENV.get(cfg["transport"])
    return {
        "transport": cfg["transport"],
        "configured": is_configured(cfg),
        "secret_env_var": env_name,
        "secret_present": bool(cfg["secret"]),
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
    if not cfg["secret"]:
        out.append(f"No credential found. Set {env_name} in the environment "
                   f"where the app runs, then restart it.")
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
