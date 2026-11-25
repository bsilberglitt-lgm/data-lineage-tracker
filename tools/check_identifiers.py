#!/usr/bin/env python3
"""Things in the frontend that compile cleanly and are still wrong.

    python3 tools/check_identifiers.py            # report
    python3 tools/check_identifiers.py --check    # exit 1 on a problem

The failure this exists for: during 0.5.2 a `useState` pair and a function were
lost from `DetailPanel` — one edit added the button that calls them and the
dialog that reads them, while the edit that was supposed to declare them never
wrote its result. `setImpact` ended up referenced and declared nowhere.

That compiles perfectly. `check_jsx.py` is a structural scan and saw nothing
wrong; every server-side suite passed because the fault is entirely in the
browser. The symptom was a blank page after sign-in — the worst kind of bug
this project produces: total, with the reason buried in the console.

**Two narrow rules, not a linter.** A first attempt tried to find every
identifier used but declared nowhere, and reported 183 false positives: JSX
*text content* reads as identifiers, and telling prose from code needs a real
JSX parser, which is exactly what is not available here (npm is blocked in the
assistant's sandbox, so ESLint is not an option for the thing making most of
these edits). So this checks only two things, both of which require unambiguous
syntax and so cannot be confused by prose:

1. Every `setSomething(...)` call has a matching declaration — normally the
   second half of a `const [something, setSomething] = useState(...)`.
2. Every `onClick={handler}` and friends names something declared.
2b. Every bare `something(` call names something declared. `endpointsOf` was
   invented in 0.6.1 and called from a drag handler; it compiled, and would
   have thrown the first time anyone dragged a bend.
3. No `\\uXXXX` escape sits in JSX *text*. Inside a quoted string or a
   template literal it is an escape; between tags it is four literal
   characters, so `<option>No \\u2014 enter details</option>` renders the
   backslash and the digits. That reached Ben in 0.6.0.

All three are places where the code compiles and the browser still does the
wrong thing, which is the category nothing else here covers.
"""
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
HTML = PROJECT / "static" / "index.html"

# Setters that come from somewhere other than a useState in this file.
KNOWN_SETTERS = {
    "setTimeout", "setInterval",            # the browser's
    "setState",                             # React's own, if ever used
}

HANDLER_ATTRS = ("onClick", "onChange", "onSubmit", "onKeyDown", "onKeyUp",
                 "onBlur", "onFocus", "onInput", "onMouseDown", "onMouseUp",
                 "onMouseMove", "onMouseEnter", "onMouseLeave", "onDragStart",
                 "onDrop", "onDragOver", "onWheel", "onScroll", "onToggle")

# `createImageBitmap` decodes an uploaded image off the main thread so the
# avatar can be downscaled before it is sent. It is a browser global, which
# is exactly what this set is for.
GLOBAL_FUNCS = {"alert", "confirm", "prompt", "fetch", "createImageBitmap"}

# Functions called in index.html that live somewhere else. React and the
# browser globals are covered by GLOBALS below; these are the project's own
# cross-file names plus the few constructors called bare.
# Keywords that can be followed directly by a parenthesis and so look like
# calls to the rule below.
KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "typeof", "void",
    "delete", "instanceof", "in", "of", "new", "await", "yield", "throw",
    "do", "else", "function", "super", "this", "import", "export",
    # A class member name, syntactically indistinguishable from a call.
    "constructor",
}

KNOWN_CALLS = {
    "require", "Number", "String", "Boolean", "Array", "Object", "Date",
    "Error", "Promise", "Map", "Set", "RegExp", "Symbol", "BigInt",
    "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "setTimeout", "clearTimeout", "setInterval",
    "clearInterval", "requestAnimationFrame", "cancelAnimationFrame",
    "structuredClone", "queueMicrotask", "btoa", "atob", "eval",
    "useState", "useEffect", "useRef", "useMemo", "useCallback",
    "useContext", "useReducer", "useLayoutEffect", "createPortal",
    # Browser constructors called with `new`.
    "FormData", "Uint8Array", "Uint16Array", "Blob", "File", "FileReader",
    "Image", "URL", "URLSearchParams", "AbortController", "TextDecoder",
    "Event", "CustomEvent", "Intl", "XMLSerializer", "DOMParser",
}


def script_body(src):
    """The inline Babel block, which is the whole application."""
    m = re.search(r'<script type="text/babel"[^>]*>(.*?)</script>', src, re.S)
    if not m:
        raise SystemExit("could not find the text/babel script block")
    return m.group(1), src[:m.start(1)].count("\n") + 1


def strip_noise(code, template_literals=False):
    """Blank comments and string literals, preserving line numbers.

    `template_literals` also blanks backtick strings. Off for the identifier
    rules, which need to see the names inside `${...}`; on for the escape rule,
    where a backtick string is exactly where an escape is legitimate.
    """
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    out = re.sub(r"//[^\n]*", blank, code)
    out = re.sub(r"/\*.*?\*/", blank, out, flags=re.S)
    out = re.sub(r'"(?:[^"\\\n]|\\.)*"', blank, out)
    out = re.sub(r"'(?:[^'\\\n]|\\.)*'", blank, out)
    if template_literals:
        out = re.sub(r"`(?:[^`\\]|\\.)*`", blank, out, flags=re.S)
    return out



def blank_template_text(code):
    """Blank the literal text of template literals, keeping `${...}` contents.

    Needed by the call rule. Blanking whole templates would hide real calls
    written inside an interpolation; leaving them alone made SVG and CSS
    function syntax look like JavaScript:

        transform={`translate(${panOffset.x},${panOffset.y}) scale(${zoom})`}
        `${created.invited} person(s) invited`

    `translate(`, `scale(` and `person(` are text there. So walk the string and
    blank only what is outside an interpolation.
    """
    out = list(code)
    i, n = 0, len(code)
    while i < n:
        if code[i] == "`":
            i += 1
            depth = 0
            while i < n:
                c = code[i]
                if c == "\\":
                    if depth == 0:
                        out[i] = " "
                        if i + 1 < n:
                            out[i + 1] = " "
                    i += 2
                    continue
                if c == "`" and depth == 0:
                    break
                if c == "$" and i + 1 < n and code[i + 1] == "{":
                    depth += 1
                    i += 2
                    continue
                if c == "}" and depth:
                    depth -= 1
                    i += 1
                    continue
                if depth == 0 and c != "\n":
                    out[i] = " "
                i += 1
            i += 1
            continue
        i += 1
    return "".join(out)

def declared_names(code):
    """Every name the file binds, over-collecting on purpose.

    The goal is to find names bound *nowhere*, so a binding this misses is a
    false positive and a binding it invents is a missed bug. Over-collecting is
    the safe direction, and the two rules above are narrow enough that it
    still catches what it is for.
    """
    names = set()
    # A single declaration may bind several names: `const NODE_W = 200,
    # NODE_H = 70, NODE_EDGE = 12`. Matching only the first left NODE_H and
    # NODE_EDGE looking undeclared, which is a false positive in the safe
    # direction for the older rules but fatal for rule 5 below.
    for m in re.finditer(r"\b(?:const|let|var)\s+([^;\n]+)", code):
        for part in m.group(1).split(","):
            part = part.split("=")[0].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", part or ""):
                names.add(part)
    for pat in (r"\bconst\s+([A-Za-z_$][\w$]*)",
                r"\blet\s+([A-Za-z_$][\w$]*)",
                r"\bvar\s+([A-Za-z_$][\w$]*)",
                r"\bfunction\s+([A-Za-z_$][\w$]*)",
                r"\bclass\s+([A-Za-z_$][\w$]*)"):
        names.update(re.findall(pat, code))

    # Array destructuring: const [a, setA] = useState(...)
    for block in re.findall(r"(?:const|let|var)\s*\[([^\]]*)\]", code):
        names.update(re.findall(r"[A-Za-z_$][\w$]*", block))
    # Object destructuring, including renames (`b: c` binds c).
    for block in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}", code):
        for part in block.split(","):
            names.add(re.sub(r".*:", "", part).strip().lstrip("."))
    # Destructured props in a component signature, and ordinary parameters.
    for block in re.findall(r"function\s*[\w$]*\s*\(([^)]*)\)", code):
        for part in re.split(r"[,{}\[\]]", block):
            part = re.sub(r".*:", "", part).strip().lstrip(".")
            if re.fullmatch(r"[A-Za-z_$][\w$]*", part or ""):
                names.add(part)
    for block in re.findall(r"\(([^()]*)\)\s*=>", code):
        for part in re.split(r"[,{}\[\]]", block):
            part = re.sub(r".*:", "", part).strip().lstrip(".")
            if re.fullmatch(r"[A-Za-z_$][\w$]*", part or ""):
                names.add(part)
    names.update(re.findall(r"(?:^|[(,=\s])([A-Za-z_$][\w$]*)\s*=>", code))
    names.discard("")
    return names


def main():
    strict = "--check" in sys.argv
    src = HTML.read_text()
    code, offset = script_body(src)
    clean = strip_noise(code)
    declared = declared_names(clean)

    problems = []

    # Rule 1 — every setX(...) call is declared. The call parenthesis is what
    # makes this unambiguous: JSX prose never contains `setFoo(`.
    seen = {}
    # `(?<![.\w$])` excludes a method call on an object — `fd.setItem(...)`
    # reads a property, not a local name.
    for m in re.finditer(r"(?<![.\w$])(set[A-Z][\w$]*)\s*\(", clean):
        seen.setdefault(m.group(1), clean[:m.start()].count("\n"))
    for name, line in sorted(seen.items(), key=lambda kv: kv[1]):
        if name in declared or name in KNOWN_SETTERS:
            continue
        problems.append((line + offset, name,
                         "called but never declared — a lost "
                         "`const [x, %s] = useState(...)`?" % name))

    # Rule 2 — every handler attribute naming a bare identifier is declared.
    attrs = "|".join(HANDLER_ATTRS)
    for m in re.finditer(r"(?<![.\w$])(?:" + attrs + r")=\{([A-Za-z_$][\w$]*)\}", clean):
        name = m.group(1)
        if name in declared or name in GLOBAL_FUNCS:
            continue
        line = clean[:m.start()].count("\n") + offset
        problems.append((line, name, "used as an event handler but never declared"))

    # Rule 4 — every bare function call names something declared. `endpointsOf`
    # was invented out of thin air in 0.6.1 and called from a drag handler: it
    # compiled, and would have thrown the first time someone dragged a bend.
    # Rules 1 and 2 did not see it, because it is neither a setter nor an
    # event-handler attribute.
    #
    # `(?<![.\w$])` skips method calls — `foo.bar(` reads a property. Requiring
    # no space before the parenthesis is what keeps prose out: JSX text says
    # "something (see below)" with a space, never "something(".
    callable_src = blank_template_text(clean)
    called = {}
    for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\(", callable_src):
        called.setdefault(m.group(1), callable_src[:m.start()].count("\n"))
    for name, line in sorted(called.items(), key=lambda kv: kv[1]):
        if name in declared or name in KNOWN_CALLS or name in KEYWORDS:
            continue
        if name in GLOBAL_FUNCS or name in KNOWN_SETTERS:
            continue
        problems.append((line + offset, name,
                         "called as a function but never declared"))

    # Rule 5 — a prop forwarded from a component that cannot see it.
    #
    # `contributors={contributors}` inside a component that never took
    # `contributors` as a prop throws ReferenceError the instant it renders,
    # React unmounts the tree, and the page goes blank. That is what
    # SchemaTableReadView and SchemaColumnReadView did from the first release
    # with a schema drill-down until Ben clicked a field in 0.7.0.
    #
    # Rules 1, 2 and 4 cannot see it — it is not a setter, not an event
    # handler, not a call — and a whole-file name check cannot either, because
    # `contributors` IS declared, in `App`. The fault is one of **scope**.
    #
    # Being scope-aware in general needs a real JS parser, which is the
    # 183-false-positive problem. But one case is decidable without one: a
    # component declared at **column zero** has no enclosing scope except the
    # module, so a bare name it forwards must be its own prop, its own local,
    # or module-level. Nested components — declared indented, inside another —
    # legitimately close over their parent and are skipped.
    module_level = set()
    for m in re.finditer(r"^(?:const|let|var)\s+([^;\n]+)", clean, re.M):
        for part in m.group(1).split(","):
            part = part.split("=")[0].strip().lstrip("{[").rstrip("}]")
            if re.fullmatch(r"[A-Za-z_$][\w$]*", part or ""):
                module_level.add(part)
    module_level.update(re.findall(r"^function\s+([A-Za-z_$][\w$]*)", clean, re.M))
    module_level.update(re.findall(r"^class\s+([A-Za-z_$][\w$]*)", clean, re.M))

    tops = [(m.start(), m.group(1))
            for m in re.finditer(r"^function\s+([A-Z][\w$]*)\s*\(", clean, re.M)]
    for idx, (pos, cname) in enumerate(tops):
        end = tops[idx + 1][0] if idx + 1 < len(tops) else len(clean)
        body = clean[pos:end]
        own = declared_names(body)
        for m in re.finditer(r"(?<![\w$.])([a-zA-Z_$][\w$]*)=\{([a-zA-Z_$][\w$]*)\}",
                             blank_template_text(body)):
            attr, value = m.group(1), m.group(2)
            if (value in own or value in module_level or value in GLOBAL_FUNCS
                    or value in KNOWN_CALLS
                    or value in ("true", "false", "null", "undefined")):
                continue
            line = clean[:pos].count("\n") + body[:m.start()].count("\n") + offset
            problems.append((line, value,
                             f"{cname} passes {attr}={{{value}}}, but {value} is "
                             f"not its prop, not its local, and not module-level "
                             f"— this throws at render and blanks the page"))

    # Rule 3 — a unicode escape outside any string is JSX text, where it is
    # not an escape at all. Checked against a copy with template literals
    # blanked as well, since those are where such an escape belongs.
    textish = strip_noise(code, template_literals=True)
    for m in re.finditer(r"\\u[0-9a-fA-F]{4}", textish):
        line = textish[:m.start()].count("\n") + offset
        problems.append((line, m.group(0),
                         "a unicode escape in JSX text renders literally — "
                         "use the character itself, or an HTML entity like "
                         "&mdash;"))

    if problems:
        print(f"FAILED — {len(problems)} problem(s)\n")
        for line, name, why in problems:
            print(f"  line {line}: {name} — {why}")
        print("\nIf one is genuinely external, add it to KNOWN_SETTERS, "
              "GLOBAL_FUNCS or KNOWN_CALLS in this file.")
        return 1 if strict else 0

    setters = len([n for n in declared if re.fullmatch(r"set[A-Z][\w$]*", n)])
    print(f"OK — setters and handlers declared, no unicode escapes in JSX text "
          f"({setters} setters, {len(declared)} names bound).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
