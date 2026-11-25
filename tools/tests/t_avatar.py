"""Profile pictures (0.10.0): upload, serve, remove, and what is refused.

    python3 tools/tests/t_avatar.py 8765 --email you@example.gov

**It uses an account of its own**, never the borrowed one. The rule in
`_client.py` is that a suite must not change the credentials of the account it
signs in as; a picture is not a credential, but it *is* the thing that account's
colleagues see beside their name, and a test that left a red square on Ben's
chip would be the same discourtesy with a smaller blast radius. A fresh address
each run, because an avatar uploaded by the previous run is a starting state
this suite would otherwise have to control.

The images are **generated here**, not committed: a 8x8 PNG written with
`zlib` and `struct` is a dozen lines, and a binary fixture in the repository
would be one more thing nobody can read in a diff.

What it protects, each a decision rather than an accident:

  * **The type comes from the bytes, never the filename.** An SVG called
    `.png` is refused — and SVG specifically, because it is a document that
    can carry script and this one is served from our own origin. The refusal
    also has to leave the existing picture alone, which is checked, because a
    rejected upload that wipes what was there is worse than one that fails.
  * **Both caps hold**, bytes and pixels. The pixel cap is the one that is
    easy to forget: a 10000x10000 PNG of flat color compresses to almost
    nothing and still has to be decoded into hundreds of megabytes of bitmap.
  * **The served response carries `nosniff` and `default-src 'none'`**, so even
    if something a browser treats as a document were ever stored, it could
    load nothing and reach nowhere.
  * **The ETag is the digest of the bytes**, which is what makes a replaced
    picture appear at once rather than after a cache expires.
  * **The user payload carries the digest and never the image.** That query
    runs on every request; selecting a quarter of a megabyte of image to
    answer "who is signed in" would be a cost paid on every API call.
  * **It needs a session.** Unlike the logo — which is a brand mark on a public
    sign-in page — this is a photograph of a member of staff, and an endpoint
    serving those anonymously is a directory of faces at a state agency.
"""
import json
import struct
import sys
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import (session, request as rq, sign_in_with, ensure_account,  # noqa: E402
                     unique_email, BASE)

PASSED, FAILED = [], []


def ck(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if not cond and detail else ""))


def png(width, height):
    """A valid truecolor PNG of the given size, red, with no dependencies."""
    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def multipart(data, filename="me.png", content_type="image/png"):
    """A multipart body, built by hand.

    `_client.request` sends JSON, and an upload cannot go through it for the
    same reason the browser's `uploadFile` exists: the body sets its own
    Content-Type, boundary and all.
    """
    bound = "----" + uuid.uuid4().hex
    body = ((f"--{bound}\r\n"
             f'Content-Disposition: form-data; name="file"; '
             f'filename="{filename}"\r\n'
             f"Content-Type: {content_type}\r\n\r\n").encode()
            + data + f"\r\n--{bound}--\r\n".encode())
    return body, f"multipart/form-data; boundary={bound}"


def upload(s, data, filename="me.png", content_type="image/png"):
    """POST one image. Returns (status, parsed body)."""
    body, ctype = multipart(data, filename, content_type)
    req = urllib.request.Request(BASE + "/api/auth/avatar", data=body,
                                 method="POST",
                                 headers={"Content-Type": ctype,
                                          "X-CSRF-Token": s.get("t") or ""})
    try:
        r = s["op"].open(req)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw[:200]}


def fetch(s, path):
    """GET something that is not JSON. Returns (status, bytes, headers)."""
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        r = s["op"].open(req)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def main():
    boss = sign_in_with(session(), rq)

    addr = unique_email("avatar")
    TEMP = "Quartz-Harbor-91#z"
    print("-- an account of this suite's own --")
    st, me, pw = ensure_account(boss, rq, addr, "Pia", "Vance", "Editor", TEMP)
    ck("it is signed in", me.get("t") is not None, f"login {st}")
    if not me.get("t"):
        print("\n  !! nothing after this means anything.\n")
        return 1
    st, who = rq(me, "GET", "/api/auth/me")
    uid = ((who or {}).get("user") or {}).get("user_id")
    ck("and we know its id", bool(uid), str(who)[:120])

    print("\n-- nothing to serve until something is uploaded --")
    ck("the payload has no digest",
       ((who or {}).get("user") or {}).get("avatar_etag") is None,
       str(((who or {}).get("user") or {}).get("avatar_etag")))
    st, _b, _h = fetch(me, f"/api/users/{uid}/avatar")
    ck("and the image endpoint is a 404", st == 404, st)

    print("\n-- an upload, and what the response carries --")
    st, r = upload(me, png(8, 8))
    ck("the upload is accepted", st == 200 and r.get("avatar_etag"), f"{st} {r}")
    etag = r.get("avatar_etag")

    st, img, hdrs = fetch(me, f"/api/users/{uid}/avatar")
    ck("the image comes back", st == 200 and img[:8] == b"\x89PNG\r\n\x1a\n", st)
    ck("typed from its bytes rather than its name",
       hdrs.get("Content-Type") == "image/png", hdrs.get("Content-Type"))
    ck("with nosniff", hdrs.get("X-Content-Type-Options") == "nosniff",
       hdrs.get("X-Content-Type-Options"))
    ck("and a policy that can load nothing",
       hdrs.get("Content-Security-Policy") == "default-src 'none'",
       hdrs.get("Content-Security-Policy"))
    ck("and an ETag that is the digest of the bytes",
       (hdrs.get("ETag") or "").strip('"') == etag, hdrs.get("ETag"))

    st, who2 = rq(me, "GET", "/api/auth/me")
    ck("the user payload carries the digest",
       ((who2 or {}).get("user") or {}).get("avatar_etag") == etag)
    ck("and never the image itself", "avatar_data" not in json.dumps(who2))

    print("\n-- replacing it changes the digest --")
    st, r2 = upload(me, png(10, 10))
    ck("the second upload is accepted", st == 200, f"{st} {r2}")
    ck("and the digest moved", r2.get("avatar_etag") not in (None, etag),
       str(r2.get("avatar_etag")))
    etag = r2.get("avatar_etag")

    print("\n-- what is refused, and what survives a refusal --")
    st, r = upload(me, b"<svg onload=alert(1)></svg>", filename="evil.png")
    ck("an SVG called .png is refused on its bytes",
       st == 400 and "PNG or JPEG" in str(r.get("error", "")), f"{st} {r}")
    st, r = upload(me, b"", filename="empty.png")
    ck("an empty file is refused", st == 400, f"{st} {r}")
    # 3001 on the long side, one past MAX_AVATAR_DIMENSION. Two rows deep so
    # the file stays small: this is the PIXEL cap being tested, and a wide flat
    # image is exactly the shape a byte cap alone would wave through.
    st, r = upload(me, png(3001, 2))
    # Asserted against the number in the message rather than just the status,
    # so raising the cap without meaning to fails here. It has been raised once
    # already, from 1500, which is how this assertion earned its keep.
    ck("over the pixel cap is refused",
       st == 400 and "3000" in str(r.get("error", "")), f"{st} {r}")
    # The byte cap, which had no check at all until the caps were doubled and
    # nothing failed to notice. A 600 KB file inside the pixel cap: the two
    # caps are separate rules and each needs its own case, or doubling one
    # while breaking the other passes.
    st, r = upload(me, png(900, 900) + b"\x00" * (600 * 1024))
    ck("over the byte cap is refused",
       st == 400 and "512 KB" in str(r.get("error", "")), f"{st} {r}")

    st, who3 = rq(me, "GET", "/api/auth/me")
    ck("and the real picture survived every refusal",
       ((who3 or {}).get("user") or {}).get("avatar_etag") == etag)

    print("\n-- the two lists carry the digest, and never the image --")
    # The Users and Contributors screens draw a circle beside each name, so
    # both payloads have to carry the digest. Checked here rather than trusted
    # because the users list used to be `SELECT u.*`, which read every avatar's
    # bytes out of the database to render a 26px circle and sent none of them.
    st, users = rq(boss, "GET", "/api/users")
    rows = users if isinstance(users, list) else []
    mine = next((u for u in rows if u.get("user_id") == uid), None)
    ck("the users list carries this account's digest",
       bool(mine) and mine.get("avatar_etag") == etag,
       str(mine.get("avatar_etag") if mine else users)[:120])
    ck("and no image bytes anywhere in it",
       "avatar_data" not in json.dumps(users))

    # A contributor's picture is its linked ACCOUNT's picture, so the list has
    # to carry both the id to fetch from and the digest. **Both or neither**:
    # an id with no digest would have the browser request an image that is a
    # 404, and a digest with no id gives it nowhere to ask.
    st, made = rq(boss, "POST", "/api/contributors",
                  {"name": "Avatar Linked", "link_user_id": uid})
    ck("a contributor can be linked to that account", st in (200, 201),
       f"{st} {str(made)[:120]}")
    st, contribs = rq(boss, "GET", "/api/contributors")
    clist = contribs if isinstance(contribs, list) else []
    linked = next((c for c in clist if c.get("account_user_id") == uid), None)
    ck("the contributor carries the account id and the digest",
       bool(linked) and linked.get("avatar_etag") == etag,
       str(linked)[:160] if linked else str(contribs)[:160])
    unlinked = [c for c in clist
                if not c.get("account_user_id") and c.get("avatar_etag")]
    ck("no contributor has a digest without an account to fetch it from",
       not unlinked, str(unlinked)[:160])

    print("\n-- it needs a session --")
    anon = session()
    anon["base"] = BASE
    st, _b, _h = fetch(anon, f"/api/users/{uid}/avatar")
    ck("anonymous is refused", st == 401, st)

    print("\n-- removal --")
    st, r = rq(me, "DELETE", "/api/auth/avatar")
    ck("it is removed", st == 200 and r.get("avatar_etag") is None, f"{st} {r}")
    st, _b, _h = fetch(me, f"/api/users/{uid}/avatar")
    ck("the image is gone", st == 404, st)
    st, who4 = rq(me, "GET", "/api/auth/me")
    ck("and so is the digest",
       ((who4 or {}).get("user") or {}).get("avatar_etag") is None)
    st, r = rq(me, "DELETE", "/api/auth/avatar")
    # "There is nothing to remove" told apart from "it worked": the second
    # press of a button should not report success at something it did not do.
    ck("removing twice says there was nothing to remove", st == 404, f"{st} {r}")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print("  FAILED:", f)
    return 0


if __name__ == "__main__":
    rc = main()
    sys.exit(rc if rc else (1 if FAILED else 0))
