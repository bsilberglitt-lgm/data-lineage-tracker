#!/usr/bin/env python3
"""Validate the JSX inside static/index.html without needing a compiler.

The whole frontend is one inline `<script type="text/babel">` block compiled in
the browser. A syntax error there produces a blank page with the reason hidden
in the browser console, so it is worth catching before shipping.

This is a scanner, not a compiler. It tracks the things that actually break the
page and that simple brace-counting gets wrong:

* JavaScript strings, template literals (including nested `${...}`), regular
  expression literals, and comments
* JSX elements — matching open/close tag names, self-closing tags, fragments
* JSX *text*, where an apostrophe in "can't" is prose rather than a quote, and
  where `{` opens an expression container

What it cannot do is type-check, resolve identifiers, or catch runtime errors.
For an authoritative check, run tools/check_jsx.sh, which compiles with the real
Babel in Docker.

Usage:
    python3 tools/check_jsx.py [path/to/index.html]
Exit code 0 = clean, 1 = problems found.
"""

import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "static" / "index.html"

# `<` starts JSX when it appears where a value is expected, not after one.
EXPRESSION_CONTEXT = set("(,=:[{;&|?!+*-%<>") | {"\n"}
# ...and also directly after these, e.g. `return <div>` or `() => <span>`.
EXPRESSION_KEYWORDS = ("return", "=>", "typeof", "case", "else", "do", "yield",
                       "await", "in", "of", "&&", "||", "??")
VOID_LIKE = {"br", "hr", "img", "input", "meta", "link", "source", "area", "col"}


class Problem(Exception):
    pass


def extract_script(html):
    m = re.search(r'<script type="text/babel"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise Problem("No <script type=\"text/babel\"> block found.")
    return m.group(1), html[:m.start(1)].count("\n") + 1


def scan(code, line_offset=0):
    """Walk the source with an explicit mode stack, returning a list of problems.

    A character heuristic is not enough: whether `<` begins a tag, and whether an
    apostrophe is a quote, both depend on whether we are currently inside JSX
    children or inside JavaScript. So we keep a stack of frames:

        ('(' | '[' | '{')  JavaScript grouping
        'jsx'              inside an element's children — text is literal
        'expr'             inside {...} within JSX children — back to JavaScript
    """
    problems = []
    stack = []               # list of [kind, name, line]
    i, n = 0, len(code)
    line = 1
    prev = ""

    def at(k):
        return code[k] if k < n else ""

    def report(msg, ln=None):
        problems.append(f"line {(ln if ln is not None else line) + line_offset}: {msg}")

    def in_jsx_text():
        return bool(stack) and stack[-1][0] == "jsx"

    def expression_position(k):
        """True if a '<' at index k can begin JSX rather than be a comparison."""
        j = k - 1
        while j >= 0 and code[j] in " \t\r\n":
            j -= 1
        if j < 0:
            return True
        if code[j] in EXPRESSION_CONTEXT:
            return True
        before = code[max(0, j - 9):j + 1]
        return any(before.endswith(kw) for kw in EXPRESSION_KEYWORDS)

    def read_tag(k):
        """Parse a tag starting at code[k] == '<'. Returns (end_index, closing,
        name, self_closing) or None."""
        m = re.match(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9_.\-]*)?", code[k:])
        if not m:
            return None
        closing = m.group(1) == "/"
        name = m.group(2) or ""          # empty name = fragment <> or </>
        j = k + m.end()
        depth = 0
        self_closing = False
        nonlocal line
        while j < n:
            c = code[j]
            if c == "\n":
                line += 1
            elif c in "\"'" and depth >= 0:
                q = c
                j += 1
                while j < n and code[j] != q:
                    if code[j] == "\\":
                        j += 1
                    elif code[j] == "\n":
                        line += 1
                    j += 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "/" and at(j + 1) == ">" and depth <= 0:
                self_closing = True
                j += 1
                break
            elif c == ">" and depth <= 0:
                break
            j += 1
        return j + 1, closing, name, self_closing

    while i < n:
        ch = code[i]

        if ch == "\n":
            line += 1
            i += 1
            continue

        # ------------------------------------------------ JSX children --
        if in_jsx_text():
            if ch == "<":
                parsed = read_tag(i)
                if parsed:
                    end, closing, name, self_closing = parsed
                    if closing:
                        if not stack or stack[-1][0] != "jsx":
                            report(f"closing </{name}> with no element open")
                        else:
                            _, open_name, open_line = stack.pop()
                            if open_name != name:
                                report(f"closing </{name}> does not match <{open_name}> "
                                       f"opened on line {open_line + line_offset}")
                    elif not self_closing and name.lower() not in VOID_LIKE:
                        stack.append(["jsx", name, line])
                    i = end
                    continue
                i += 1
                continue
            if ch == "{":
                stack.append(["expr", None, line])
                i += 1
                continue
            if ch == "}":
                # A bare '}' in JSX text is a syntax error in real JSX; every
                # legitimate one is consumed when its expression container closes.
                report("stray '}' in JSX text (write &rbrace; if you meant a "
                       "literal brace)")
                i += 1
                continue
            # Anything else is literal text: apostrophes, quotes, slashes.
            i += 1
            continue

        # ---------------------------------------------------- JavaScript --
        if ch == "/" and at(i + 1) == "/":
            while i < n and code[i] != "\n":
                i += 1
            continue
        if ch == "/" and at(i + 1) == "*":
            j = code.find("*/", i + 2)
            if j == -1:
                report("unterminated /* comment")
                break
            line += code[i:j].count("\n")
            i = j + 2
            continue

        if ch == "/" and (prev == "" or prev in EXPRESSION_CONTEXT):
            j, closed = i + 1, False
            while j < n:
                c = code[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "\n":
                    break
                if c == "[":
                    while j < n and code[j] != "]":
                        if code[j] == "\\":
                            j += 1
                        j += 1
                elif c == "/":
                    closed = True
                    break
                j += 1
            if closed:
                i = j + 1
                prev = "/"
                continue

        if ch in "\"'":
            quote, j = ch, i + 1
            ok = False
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "\n":
                    break
                if code[j] == quote:
                    ok = True
                    break
                j += 1
            if not ok:
                report(f"unterminated {quote} string")
            i = j + 1
            prev = quote
            continue

        if ch == "`":
            j, depth = i + 1, 0
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "\n":
                    line += 1
                elif code[j] == "$" and at(j + 1) == "{":
                    depth += 1
                    j += 1
                elif code[j] == "}" and depth:
                    depth -= 1
                elif code[j] == "`" and depth == 0:
                    break
                j += 1
            if j >= n:
                report("unterminated template literal")
                break
            i = j + 1
            prev = "`"
            continue

        if ch == "<" and expression_position(i):
            parsed = read_tag(i)
            if parsed:
                end, closing, name, self_closing = parsed
                if closing:
                    report(f"closing </{name}> outside of any element")
                elif not self_closing and name.lower() not in VOID_LIKE:
                    stack.append(["jsx", name, line])
                i = end
                prev = ">"
                continue

        if ch in "([{":
            stack.append([ch, None, line])
        elif ch in ")]}":
            want = {")": "(", "]": "[", "}": "{"}[ch]
            if not stack:
                report(f"stray '{ch}'")
            elif ch == "}" and stack[-1][0] == "expr":
                stack.pop()                      # end of a JSX expression container
            elif stack[-1][0] == want:
                stack.pop()
            else:
                kind, name, oline = stack[-1]
                label = f"<{name}>" if kind == "jsx" else f"'{kind}'"
                report(f"'{ch}' does not close {label} opened on line "
                       f"{oline + line_offset}")
                stack.pop()

        if not ch.isspace():
            prev = ch
        i += 1

    for kind, name, ln in stack[-6:]:
        if kind == "jsx":
            problems.append(f"line {ln + line_offset}: <{name}> is never closed")
        elif kind == "expr":
            problems.append(f"line {ln + line_offset}: '{{' in JSX is never closed")
        else:
            problems.append(f"line {ln + line_offset}: '{kind}' is never closed")
    return problems


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        print(f"Not found: {path}")
        return 2
    html = path.read_text()
    try:
        code, offset = extract_script(html)
    except Problem as e:
        print(str(e))
        return 1

    problems = scan(code, offset - 1)

    # Cheap sanity checks that have bitten this project before.
    extra = []
    if "@babel/standalone@" not in html:
        extra.append("Babel is not pinned to a version — an unpinned Babel once "
                     "switched to the automatic JSX runtime and blanked the page.")
    if re.search(r"^\s*import\s", code, re.M):
        extra.append("An `import` statement is present. This script runs as a "
                     "classic script and cannot use ES modules.")

    if problems or extra:
        print(f"FAILED — {len(problems) + len(extra)} problem(s) in {path.name}\n")
        for p in problems[:40]:
            print(f"  {p}")
        for e in extra:
            print(f"  {e}")
        return 1

    print(f"OK — JSX in {path.name} looks structurally sound "
          f"({code.count(chr(10))} lines scanned).")
    print("Note: this checks syntax only. Run tools/check_jsx.sh for a real "
          "Babel compile, and click through the UI for rendering bugs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
