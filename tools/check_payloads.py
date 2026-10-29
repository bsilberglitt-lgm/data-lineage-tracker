#!/usr/bin/env python3
"""Does the browser send every field the server will accept?

    python3 tools/check_payloads.py            # report
    python3 tools/check_payloads.py --check     # exit 1 on a gap (pre-commit)

`DetailPanel.saveEdit` in static/index.html builds each record type's PUT body
as a hand-written object literal. The server's update handlers write only the
keys they are sent (`for key in self.FLOW_FIELDS: if key in data`). So a field
the server accepts but the browser never sends is **silently unsaveable**: the
control appears, the dropdown works, the save returns 200, and the value never
changes. Nothing errors, and no existing test notices.

That is what happened to **Owner on a data flow**. It was in the Add window, in
the edit view and in the read view, and absent from one object literal — so you
could set an Owner when creating a flow and never change it afterwards. Source
of Truth on a data element had the same hole. Both reached Ben.

This is the `field_catalog.py` lesson in a different place: a hand-kept list
that has to track another hand-kept list drifts, and the failure is silent. The
catalogue was fixed by generating it. These payloads could be generated too,
but they are only five literals and generating them would mean teaching a tool
the per-type shape of a save; comparing them is cheaper and catches the same
failure.

Deliberately one-directional. A gap — the server accepts it, the browser never
sends it — is a bug. The reverse is not: the browser sends `proposed`, which no
field tuple contains because the server handles it separately, and x_pos/y_pos
are written by the drag endpoint rather than by a save.

Fields that are genuinely not meant to be editable from the flyout go in
DORMANT below, with a reason. Keeping them listed rather than ignored means a
field that becomes editable has to be taken off the list deliberately.
"""
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
HTML = PROJECT / "static" / "index.html"
APP = PROJECT / "app.py"

# Accepted by the server, deliberately not sent from the details panel.
# Each needs a reason, and the reason has to be about the UI rather than about
# this checker — "not drawn anywhere" is a fact that can change.
DORMANT = {
    ("system", "owner_id"):
        "No Owner control on the system edit view; only Data Steward is drawn.",
    ("flow", "agreement_id"):
        "Agreements management screen is not built yet, so nothing sets this.",
    ("system", "x_pos"):
        "Diagram position, written by the drag handler, not by a save.",
    ("system", "y_pos"):
        "Diagram position, written by the drag handler, not by a save.",
}

# The server tuple that governs each record type's update.
SERVER_TUPLES = {
    "system": "SYSTEM_FIELDS",
    "flow": "FLOW_FIELDS",
    "element": "ELEMENT_FIELDS",
}


def browser_payloads(src):
    """{type: {field, ...}} from the object literals in DetailPanel.saveEdit."""
    start = src.index("const saveEdit")
    block = src[start:src.index("const result = await api.put", start)]
    out = {}
    for m in re.finditer(
            r"type === '(\w+)'[^\n]*\n\s*url = [^\n]*\n\s*saveData = \{(.*?)\n      \};",
            block, re.S):
        out[m.group(1)] = set(re.findall(r"(\w+):\s*form\.", m.group(2)))
    # The two schema levels share one branch and one field list.
    m = re.search(r"type === 'schema_table' \|\| type === 'schema_column'"
                  r".*?saveData = \{(.*?)\n      \};", block, re.S)
    if m:
        out["schema"] = set(re.findall(r"(\w+):\s*form\.", m.group(1)))
    return out


def server_fields(src):
    """{type: {field, ...}} the update handlers will write."""
    out = {}
    for kind, name in SERVER_TUPLES.items():
        m = re.search(name + r"\s*=\s*\((.*?)\)", src, re.S)
        if not m:
            raise SystemExit(f"could not find {name} in app.py")
        out[kind] = set(re.findall(r"'([a-z_0-9]+)'", m.group(1)))
    # Schema governance is a list comprehension over GOV_FIELDS plus extras.
    gov = re.search(r"GOV_FIELDS = \[(.*?)\n\]", src, re.S)
    extra = re.search(r"GOV_WRITABLE = \[f for f, _ in GOV_FIELDS\] \+ \[(.*?)\]", src)
    if not (gov and extra):
        raise SystemExit("could not find GOV_FIELDS / GOV_WRITABLE in app.py")
    out["schema"] = ({f for f, _ in re.findall(r'\("(\w+)",\s*("?\w*"?|None)\)',
                                               gov.group(1))}
                     | set(re.findall(r'"(\w+)"', extra.group(1))))
    return out


def main():
    strict = "--check" in sys.argv
    sent = browser_payloads(HTML.read_text())
    accepts = server_fields(APP.read_text())

    problems, dormant_seen = [], set()
    for kind in sorted(accepts):
        gap = accepts[kind] - sent.get(kind, set())
        for field in sorted(gap):
            if (kind, field) in DORMANT:
                dormant_seen.add((kind, field))
                continue
            problems.append(
                f"{kind}.{field}: the server will save it and the details panel "
                f"never sends it, so editing it does nothing. Add it to the "
                f"{kind} saveData in DetailPanel, or to DORMANT in this file "
                f"with a reason.")

    stale = sorted(set(DORMANT) - dormant_seen)
    for kind, field in stale:
        problems.append(
            f"{kind}.{field} is listed as dormant but is no longer missing — "
            f"remove it from DORMANT in tools/check_payloads.py.")

    if problems:
        print(f"FAILED — {len(problems)} problem(s)\n")
        for p in problems:
            print("  " + p)
        return 1 if strict else 0

    total = sum(len(v) for v in sent.values())
    print(f"OK — every field the server accepts is sent by the details panel "
          f"({total} fields across {len(sent)} record types, "
          f"{len(DORMANT)} dormant by design).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
