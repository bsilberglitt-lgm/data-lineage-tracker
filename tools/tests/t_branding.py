"""0.4.2 — the uploadable logo.

What this pins down, in order of how much it would matter if it broke:

  1. **Only images get stored.** The check is on the file's contents, not its
     name, so an HTML or SVG file renamed .png is still refused. If this
     regressed, the application would serve attacker-chosen bytes from its own
     origin.
  2. **Only a SuperAdmin can change it**, and the image itself is readable
     without a session — the sign-in screen needs it, and it is the one
     deliberate exception to requiring a session.
  3. Both caps hold: 512 KB, and 2000 pixels on a side. The second is the one
     a byte cap alone would miss.
  4. The served content type comes from the bytes, and the ETag changes when
     the image does — otherwise a replaced logo would appear not to have
     changed until a hard refresh.

Run against the sandbox harness (no PostgreSQL needed):

    python3 tools/tests/t_branding.py 8900
"""
import io, json, struct, sys, urllib.request, urllib.error, http.cookiejar, uuid, zlib
import os



# Credentials and signing in come from _client.py — no hardcoded password, and
# nothing here changes one. This suite keeps its own `rq`, so it borrows just
# the sign-in; see the comment at the top of _client.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _client import sign_in_with, BASE as B  # noqa: E402
def S():
    j = http.cookiejar.CookieJar()
    return {"op": urllib.request.build_opener(urllib.request.HTTPCookieProcessor(j)),
            "t": None}


def rq(s, m, p, b=None, headers=None):
    d = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(B + p, data=d, method=m)
    if d:
        r.add_header("Content-Type", "application/json")
    if s["t"] and m != "GET":
        r.add_header("X-CSRF-Token", s["t"])
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with s["op"].open(r, timeout=60) as x:
            raw = x.read()
            ctype = x.headers.get("Content-Type", "")
            if "json" in ctype:
                return x.status, json.loads(raw.decode() or "null"), dict(x.headers)
            return x.status, raw, dict(x.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except Exception:
            return e.code, raw[:200], dict(e.headers)


def upload(s, url, filename, content, mime="image/png"):
    bnd = "----b" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; "
               f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    body.write(content)
    body.write(f"\r\n--{bnd}--\r\n".encode())
    r = urllib.request.Request(B + url, data=body.getvalue(), method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
    if s["t"]:
        r.add_header("X-CSRF-Token", s["t"])
    try:
        with s["op"].open(r, timeout=60) as x:
            return x.status, json.loads(x.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]


def png(w, h, pad=0):
    """A real, structurally valid PNG of the given dimensions."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + \
               struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">II", w, h) +
                                       b"\x08\x06\x00\x00\x00")
    if pad:                                   # inflate the file without changing w/h
        out += chunk(b"tEXt", b"pad\x00" + b"x" * pad)
    return out + chunk(b"IEND", b"")


P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


boss = sign_in_with(S(), rq)

print("-- nothing uploaded yet --")
st, r, _ = rq(boss, "GET", "/api/branding")
ck("branding reports no logo", st == 200 and r.get("logo") is None, f"{st} {str(r)[:90]}")
st, r, _ = rq(boss, "GET", "/api/branding/logo")
ck("the image endpoint 404s", st == 404, str(st))
st, r, _ = rq(boss, "GET", "/api/auth/me")
ck("settings carry no logo etag", (r.get("settings") or {}).get("logo_etag") is None,
   str((r.get("settings") or {}).get("logo_etag")))

print("\n-- what is not an image is refused --")
for label, name, data, mime in [
        ("HTML renamed .png", "logo.png", b"<!doctype html><script>alert(1)</script>",
         "image/png"),
        ("SVG renamed .png", "logo.png",
         b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "image/png"),
        ("GIF", "logo.gif", b"GIF89a" + b"\x00" * 40, "image/gif"),
        ("PDF claiming to be a PNG", "logo.png", b"%PDF-1.7\n%\xc7\xec", "image/png"),
        ("an empty file", "logo.png", b"", "image/png")]:
    st, r = upload(boss, "/api/branding", name, data, mime)
    ck(f"{label} is refused", st == 400, f"{st} {str(r)[:80]}")

print("\n-- the caps --")
st, r = upload(boss, "/api/branding", "big.png", png(64, 64, pad=600 * 1024))
ck("over 512 KB is refused", st == 400 and "KB" in str(r), f"{st} {str(r)[:90]}")
st, r = upload(boss, "/api/branding", "huge.png", png(4000, 30))
ck("over 2000px is refused even though the file is tiny",
   st == 400 and "pixels" in str(r), f"{st} {str(r)[:90]}")
ck("...and nothing was stored by the refusals",
   rq(boss, "GET", "/api/branding")[1].get("logo") is None)

print("\n-- a real upload --")
st, r = upload(boss, "/api/branding", "maine.png", png(240, 80))
logo = (r or {}).get("logo") or {}
ck("a valid PNG is accepted", st == 200, f"{st} {str(r)[:90]}")
ck("its dimensions are measured", (logo.get("width"), logo.get("height")) == (240, 80),
   f"{logo.get('width')}x{logo.get('height')}")
ck("the type comes from the bytes", logo.get("content_type") == "image/png",
   str(logo.get("content_type")))
first_etag = logo.get("etag")
ck("an etag was recorded", bool(first_etag))

st, body, hdr = rq(boss, "GET", "/api/branding/logo")
ck("the image is served back", st == 200 and body[:8] == b"\x89PNG\r\n\x1a\n", str(st))
ck("served as image/png", hdr.get("Content-Type") == "image/png",
   str(hdr.get("Content-Type")))
ck("with nosniff, so it cannot be reinterpreted",
   hdr.get("X-Content-Type-Options") == "nosniff", str(hdr.get("X-Content-Type-Options")))
ck("and revalidated rather than cached outright",
   "no-cache" in (hdr.get("Cache-Control") or ""), str(hdr.get("Cache-Control")))
st, _, _ = rq(boss, "GET", "/api/branding/logo",
              headers={"If-None-Match": f'"{first_etag}"'})
ck("an unchanged logo answers 304", st == 304, str(st))

print("\n-- replacing it --")
st, r = upload(boss, "/api/branding", "other.png", png(120, 120))
second = (r or {}).get("logo") or {}
ck("replacing works", st == 200 and second.get("width") == 120, f"{st} {second.get('width')}")
ck("the etag changed, so a cached copy is bypassed",
   second.get("etag") and second.get("etag") != first_etag, "etag did not change")
st, _, _ = rq(boss, "GET", "/api/branding/logo",
              headers={"If-None-Match": f'"{first_etag}"'})
ck("the old etag no longer matches", st == 200, str(st))
st, r, _ = rq(boss, "GET", "/api/auth/me")
ck("settings now carry the new etag",
   (r.get("settings") or {}).get("logo_etag") == second.get("etag"))

print("\n-- position is validated --")
st, r, _ = rq(boss, "PUT", "/api/preferences", {"values": {"logo_position": "sideways"}})
ck("a junk position is refused", st == 400, f"{st} {str(r)[:80]}")
st, r, _ = rq(boss, "PUT", "/api/preferences", {"values": {"logo_position": "above"}})
ck("a real one is accepted", st == 200, f"{st} {str(r)[:80]}")
st, r, _ = rq(boss, "GET", "/api/auth/me")
ck("and reaches the settings payload",
   (r.get("settings") or {}).get("logo_position") == "above",
   str((r.get("settings") or {}).get("logo_position")))

print("\n-- who may do what --")
anon = S()
st, body, hdr = rq(anon, "GET", "/api/branding/logo")
ck("the image is readable without signing in (the sign-in screen needs it)",
   st == 200 and body[:4] == b"\x89PNG", str(st))
st, r, _ = rq(anon, "GET", "/api/branding")
ck("but its details are not", st in (401, 403), str(st))

# The sign-in screen has no session, so it needs its own settings endpoint.
# Without it the configured title never appears there at all.
st, pub, _ = rq(anon, "GET", "/api/branding/public")
ck("the sign-in screen can read the branding", st == 200, str(st))
ck("...including the title", pub.get("app_title") is not None, str(pub)[:80])
ck("...and the logo etag and position",
   "logo_etag" in pub and "logo_position" in pub, str(sorted(pub))[:90])
ck("but not the password policy", "password_policy" not in pub, str(sorted(pub))[:90])
st, r = upload(anon, "/api/branding", "x.png", png(10, 10))
ck("and a stranger cannot upload one", st in (401, 403), f"{st} {str(r)[:80]}")

# The password must not contain the account holder's name — the policy
# rejects that, and a "Viewer" named password fails for that reason alone.
temp_email, temp_pass = "logo.reader@maine.gov", "Zephyr-Bright-52#q"
st, existing, _ = rq(boss, "GET", "/api/users")
found = next((u for u in existing if u["email"] == temp_email), None) \
    if isinstance(existing, list) else None
if found:
    rq(boss, "PUT", f"/api/users/{found['user_id']}",
       {"is_active": 1, "password": temp_pass, "role": "Viewer"})
else:
    rq(boss, "POST", "/api/users", {"email": temp_email, "first_name": "Casey",
                                    "last_name": "Reader", "role": "Viewer",
                                    "password": temp_pass})
viewer = S()
st, vb, _ = rq(viewer, "POST", "/api/auth/login",
               {"email": temp_email, "password": temp_pass})
if st == 200:
    viewer["t"] = vb["csrf_token"]
    if vb["user"].get("must_change_password"):
        rq(viewer, "POST", "/api/auth/change-password",
           {"current_password": temp_pass, "new_password": "Amber-Kestrel-73#w"})
    st, r = upload(viewer, "/api/branding", "x.png", png(10, 10))
    ck("a Viewer cannot upload a logo", st == 403, f"{st} {str(r)[:80]}")
    st, r, _ = rq(viewer, "DELETE", "/api/branding")
    ck("a Viewer cannot remove one", st == 403, f"{st} {str(r)[:80]}")
else:
    ck("viewer account could be created", False, f"login {st}")

print("\n-- the audit trail --")
st, ev, _ = rq(boss, "GET", "/api/audit?entity_type=branding")
rows = ev.get("events", ev) if isinstance(ev, dict) else ev
summaries = [e.get("summary", "") for e in rows] if isinstance(rows, list) else []
ck("uploading and replacing were both recorded", len(summaries) >= 2, str(summaries[:3]))
ck("the summary says what was uploaded, not the bytes",
   any("240x80" in s or "120x120" in s for s in summaries), str(summaries[:3]))
ck("no audit row carries the image itself",
   not any("PNG" in s and len(s) > 400 for s in summaries))

print("\n-- removing it --")
st, r, _ = rq(boss, "DELETE", "/api/branding")
ck("a SuperAdmin can remove the logo", st == 200, f"{st} {str(r)[:80]}")
st, _, _ = rq(boss, "GET", "/api/branding/logo")
ck("the image endpoint 404s again", st == 404, str(st))
st, r, _ = rq(boss, "GET", "/api/auth/me")
ck("the etag is gone from settings",
   (r.get("settings") or {}).get("logo_etag") is None)
st, r, _ = rq(boss, "DELETE", "/api/branding")
ck("removing when there is none is refused with a reason", st == 404, f"{st} {str(r)[:80]}")

print(f"\n  {len(P)} passed, {len(F)} failed")
if F:
    print("  FAILURES:", F)
    sys.exit(1)
