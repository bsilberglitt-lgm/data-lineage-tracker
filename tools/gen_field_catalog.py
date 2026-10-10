#!/usr/bin/env python3
"""Generate field_catalog.py from static/index.html.

    python3 tools/gen_field_catalog.py            # write field_catalog.py
    python3 tools/gen_field_catalog.py --check    # fail if it would change

**Why generated rather than hand-written.** Field definitions need a list of
every field a record has, with enough context to pick one out of ninety in a
dropdown. Hand-maintaining that list guarantees it drifts from the UI: someone
adds a field, forgets the list, and the new field silently cannot be defined.
Every field in `index.html` already states its own database column at the call
site, so the list can be derived instead and cannot disagree with what is
actually rendered.

**Why keyed on the column, not the label.** The same column is labelled
differently depending on where it appears:

    system.encryption_at_rest    read view "Encryption at Rest"
                                 edit view "Encrypt at Rest"
                                 add modal "Encryption at Rest"
    data_flow.transfer_method    edit view "Method"
                                 add modal "Transfer Method"

Ten columns differ between the flyout and the Add modals, and six differ
between the read and edit views of the flyout itself. A catalogue keyed on
label would therefore treat one field as several, and a definition written
against one spelling would not appear beside the others. The column is the
thing that is actually stable, so each entry records the column once and lists
whatever label each surface happens to use.

**The labels are left alone.** It would be tidier if every surface agreed, but
rewording what people already read is a separate decision from adding
definitions. The canonical name — the one Preferences shows — is the edit
view's, as Ben chose; the others are recorded as aliases so a definition finds
its field wherever it is drawn.

What this cannot see: fields rendered by a component that does not state its
column, and sections built any way other than `CollapsibleSection title="..."`.
Both are checked — `--check` fails rather than quietly emitting a short list.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")
OUT = os.path.join(ROOT, "field_catalog.py")

# The record types that get definitions. Schema governance, preferences, users
# and the scenario dialogs are deliberately out of scope: those are
# administrative screens rather than descriptions of the department's data.
VIEWS = [
    ("system", "SystemEditView", "SystemReadView", "AddSystemModal"),
    ("data_flow", "FlowEditView", "FlowReadView", "AddFlowModal"),
    ("data_element", "ElementEditView", "ElementReadView", None),
    ("contributor", "ContributorEditView", "ContributorReadView",
     "AddContributorModal"),
]

# Fields nothing should offer a definition for: either not a field at all, or
# a scenario mechanic explained by the scenario help articles.
SKIP_COLUMNS = {"proposed"}


def read_source():
    with open(INDEX, encoding="utf-8") as fh:
        return fh.read()


# Components are declared two ways in index.html — `function Name(...)` and
# `const Name = (...) => (...)` — and some are nested inside others. Brace
# matching is not usable: for an arrow component the first `{` belongs to the
# destructured props, not the body.
#
# So: find where every component starts, and take each one's text up to
# wherever the next one begins. Crude, but it cannot be fooled by a brace in a
# parameter list, and a wrong boundary shows up immediately as a field count
# that disagrees with the UI.
COMPONENT_START = re.compile(
    r"^[ \t]*(?:function|const)\s+([A-Z]\w+)\b", re.MULTILINE)


def component_bounds(src):
    """{name: (start, end)} for every component in the file."""
    marks = [(m.start(), m.group(1)) for m in COMPONENT_START.finditer(src)]
    marks.sort()
    out = {}
    for idx, (pos, name) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(src)
        # Keep the first declaration if a name somehow appears twice.
        out.setdefault(name, (pos, end))
    return out


_BOUNDS = {}


def component(src, name):
    global _BOUNDS
    if not _BOUNDS:
        _BOUNDS = component_bounds(src)
    span = _BOUNDS.get(name)
    return src[span[0]:span[1]] if span else None


SECTION_TOKEN = "@@SECTION@@"

# Components that render one labelled field. `EditField` covers most of them;
# `ContributorSelect` is a person picker and `ContributorField` its read-only
# twin, and both were missed when this generator only knew about EditField —
# six fields, Data Steward among them, were silently absent from the catalogue
# and so could not be given a definition. Anything else carrying a `label=` is
# now reported rather than skipped; see `unknown_field_components`.
EDIT_COMPONENTS = ("EditField", "ContributorSelect")
READ_COMPONENTS = ("ReadField", "ContributorField")
KNOWN_COMPONENTS = set(EDIT_COMPONENTS) | set(READ_COMPONENTS)


def unknown_field_components(body):
    """Names of labelled components this generator does not understand.

    A silent omission is the dangerous failure here: the catalogue simply comes
    up short, Preferences offers fewer fields than the app has, and nothing
    anywhere says so. Returning them lets --check fail instead.
    """
    flat = re.sub(r"\s+", " ", body)
    found = set()
    for m in re.finditer(r"<([A-Z]\w+)\b[^>]*?label=", flat):
        if m.group(1) not in KNOWN_COMPONENTS:
            found.add(m.group(1))
    return found


def edit_fields(body):
    """[(column, label, section)] from an edit view, in render order.

    `<EditField>` is written both on one line and across several, so the body
    is flattened before matching — a line-by-line regex silently missed about a
    third of the calls, which would have left a third of the fields
    undefinable with no error anywhere. Section headings are replaced with a
    token first so that flattening does not lose where each one started.
    """
    marked = re.sub(r'<CollapsibleSection\s+title="([^"]+)"',
                    SECTION_TOKEN + r"\1" + SECTION_TOKEN, body)
    flat = re.sub(r"\s+", " ", marked)

    out, section = [], None
    pos = 0
    pattern = re.compile(
        SECTION_TOKEN + r"(.*?)" + SECTION_TOKEN +
        r"|<(?:" + "|".join(EDIT_COMPONENTS) + r")\b(.*?)/>")
    for m in pattern.finditer(flat):
        if m.group(1) is not None:
            section = m.group(1).strip()
            continue
        attrs = m.group(2) or ""
        label = re.search(r'label="([^"]+)"', attrs)
        col = re.search(r"set\('([a-z_0-9]+)'", attrs)
        if label and col:
            out.append((col.group(1), label.group(1), section))
    return out


def read_labels(body):
    """{column: label} from a read view."""
    out = {}
    flat = re.sub(r"\s+", " ", body)
    for call in re.finditer(
            r"<(?:" + "|".join(READ_COMPONENTS) + r")\b(.*?)/>", flat):
        attrs = call.group(1)
        label = re.search(r'label="([^"]+)"', attrs)
        col = re.search(r"data\.([a-z_0-9]+)", attrs)
        if label and col:
            out.setdefault(col.group(1), label.group(1))
    return out


def modal_labels(body):
    """{column: label} from an Add modal: form-groups and ReqFields alike."""
    out = {}
    flat = re.sub(r"\s+", " ", body)
    for block in re.findall(r'<div className="form-group">(.*?)</div>', flat):
        label = re.search(r"<label[^>]*>([^<]+)</label>", block)
        col = re.search(r"set\('([a-z_0-9]+)'", block)
        if label and col:
            out.setdefault(col.group(1), label.group(1).strip().rstrip("*").strip())
    for call in re.finditer(r"<ReqField\b(.*?)/>", flat):
        attrs = call.group(1)
        label = re.search(r'label="([^"]+)"', attrs)
        col = re.search(r"set\('([a-z_0-9]+)'", attrs)
        if label and col:
            out.setdefault(col.group(1), label.group(1).strip().rstrip("*").strip())
    return out


def build():
    src = read_source()
    entries, problems = [], []

    for entity, ev, rv, am in VIEWS:
        ebody = component(src, ev)
        if not ebody:
            problems.append(f"{entity}: edit view {ev} not found")
            continue
        fields = edit_fields(ebody)
        if not fields:
            problems.append(f"{entity}: no fields found in {ev}")
        for comp_name in sorted(unknown_field_components(ebody)):
            problems.append(
                f"{entity}: {ev} renders fields with <{comp_name}>, which this "
                f"generator does not know about \u2014 add it to EDIT_COMPONENTS "
                f"or those fields cannot be defined")
        rbody = component(src, rv) or ""
        for comp_name in sorted(unknown_field_components(rbody)):
            problems.append(
                f"{entity}: {rv} renders fields with <{comp_name}> \u2014 add it "
                f"to READ_COMPONENTS")
        reads = read_labels(rbody)
        modals = modal_labels(component(src, am) or "") if am else {}

        seen = set()
        for col, label, section in fields:
            if col in SKIP_COLUMNS or col in seen:
                continue
            seen.add(col)
            if not section:
                problems.append(f"{entity}.{col}: no enclosing section")
            entries.append({
                "entity": entity,
                "column": col,
                "label": label,                       # canonical: the edit view
                "section": section or "Other",
                "read_label": reads.get(col),
                "modal_label": modals.get(col),
            })

    return entries, problems


def render(entries):
    lines = [
        '"""The fields a record has, and what each surface calls them.',
        "",
        "GENERATED by tools/gen_field_catalog.py from static/index.html.",
        "Do not edit by hand — regenerate it:",
        "",
        "    python3 tools/gen_field_catalog.py",
        "",
        "Keyed on (entity, column) because the read view, the edit view and the",
        "Add modal label the same column differently; see the generator for the",
        "list. `label` is the canonical name, taken from the edit view, and is",
        "what Preferences shows. `read_label` and `modal_label` are the other",
        "spellings, so a definition is found wherever its field is drawn.",
        '"""',
        "",
        "FIELDS = [",
    ]
    for e in entries:
        lines.append("    {")
        for k in ("entity", "column", "label", "section", "read_label", "modal_label"):
            lines.append(f"        {k!r}: {e[k]!r},")
        lines.append("    },")
    lines += [
        "]",
        "",
        "",
        "def by_entity():",
        '    """{entity: [field, ...]} in the order the edit view renders them."""',
        "    out = {}",
        "    for f in FIELDS:",
        '        out.setdefault(f["entity"], []).append(f)',
        "    return out",
        "",
        "",
        "def label_index():",
        '    """{(entity, label): column} for every spelling of every field.',
        "",
        "    This is what lets a component look itself up knowing only the label",
        "    it was given, without every call site having to name its column.",
        '    """',
        "    out = {}",
        "    for f in FIELDS:",
        '        for key in ("label", "read_label", "modal_label"):',
        "            if f.get(key):",
        '                out[(f["entity"], f[key])] = f["column"]',
        "    return out",
        "",
    ]
    return "\n".join(lines)


def main():
    entries, problems = build()
    text = render(entries)

    if "--check" in sys.argv:
        current = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if problems:
            print("field catalogue: PROBLEMS")
            for p in problems:
                print("  -", p)
            return 1
        if current != text:
            print("field_catalog.py is out of date — run "
                  "`python3 tools/gen_field_catalog.py`")
            return 1
        print(f"field catalogue up to date ({len(entries)} fields)")
        return 0

    for p in problems:
        print("  warning:", p)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    per = {}
    for e in entries:
        per[e["entity"]] = per.get(e["entity"], 0) + 1
    print(f"wrote {OUT} — {len(entries)} fields: " +
          ", ".join(f"{k} {v}" for k, v in sorted(per.items())))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
