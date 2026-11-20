#!/usr/bin/env bash
# Compile static/index.html's JSX with the real Babel, in Docker.
#
# This is the authoritative check. tools/check_jsx.py is a fast structural
# scanner that runs anywhere; this one runs the same compiler the browser uses,
# so it catches anything Babel would reject.
#
#   ./tools/check_jsx.sh
#
# Requires Docker (already needed for local development). Nothing is installed
# on your machine and nothing is written into the project: the project is
# mounted read-only and the work happens in a throwaway container.
#
# ---------------------------------------------------------------------------
# This used to run `npm install @babel/core@<pinned> @babel/preset-react`, and
# in November 2026 that stopped working everywhere at once.
#
# The preset was deliberately left un-pinned, on the reasoning that it has its
# own version line. That held until **Babel 8 was published**: `*` then
# resolved to @babel/preset-react@8.0.1, which declares a peer dependency on
# @babel/core@^8, and the pinned 7.23.5 beside it makes a tree npm cannot
# resolve. The failure is ERESOLVE, it had nothing to do with the app, and it
# arrived without anybody touching this repository.
#
# **An un-pinned dependency is a decision that something outside the project
# gets to change later.** That is the lesson, and it is the same argument this
# project already makes for pinning React and Babel in the page itself.
#
# So this no longer uses npm at all. @babel/standalone is ONE CommonJS file
# with no runtime dependencies - every plugin and preset is a devDependency,
# bundled into the published build - so it can simply be downloaded and
# required. Three things get better:
#
#   - No registry resolution, so no peer-dependency arithmetic to go stale,
#     and no npm install to sit there (which it did, at checklist step 1b).
#   - It is the EXACT artifact the browser loads, rather than a reconstruction
#     of it from two separately versioned packages.
#   - The version is read from static/index.html, which is the real single
#     source of truth. There is no longer a constant here to disagree with it.
#
# Downloading is done with Node's own fetch, because busybox wget and curl are
# not both reliably present in a slim Node image and Node 22 has fetch built
# in.

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(grep -oE '@babel/standalone@[0-9]+\.[0-9]+\.[0-9]+' static/index.html \
          | head -1 | cut -d'@' -f3)
if [ -z "$VERSION" ]; then
  echo "Could not find a pinned @babel/standalone version in static/index.html."
  echo "If the script tag was changed or the version un-pinned, that is the"
  echo "thing to look at — CLAUDE.md explains why it must stay pinned."
  exit 2
fi

echo "Compiling static/index.html with @babel/standalone ${VERSION} (via Docker)…"
echo "(the same build the page loads; first run pulls a small Node image)"
echo

# Node 22, not 20: everything before 22.x is end-of-life and was not patched
# for the October 2026 TLS and Brotli issues. This container does make one
# outbound HTTPS request, so that matters more here than for the geometry
# suites.
docker run --rm -v "$PWD":/work:ro -w /work \
  -e BABEL_VERSION="$VERSION" node:22-alpine node -e '
  const fs = require("fs");
  const VERSION = process.env.BABEL_VERSION;

  (async () => {
    const url = `https://unpkg.com/@babel/standalone@${VERSION}/babel.min.js`;
    let js;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      js = await res.text();
    } catch (e) {
      console.error(`Could not download ${url}`);
      console.error(String(e.message || e));
      console.error("");
      console.error("This is a network problem, not a problem with the app.");
      process.exit(2);
    }
    // A CDN answering with an error page instead of a 404 would otherwise
    // surface as a baffling syntax error inside require().
    if (js.length < 100000) {
      console.error(`Downloaded only ${js.length} bytes — too small to be Babel.`);
      console.error(js.slice(0, 300));
      process.exit(2);
    }
    fs.writeFileSync("/tmp/babel.min.js", js);

    // Standalone is UMD: it sets module.exports under CommonJS and also
    // assigns a global. Accept either rather than depending on which one a
    // future build prefers.
    const loaded = require("/tmp/babel.min.js");
    const Babel = (loaded && loaded.transform) ? loaded : global.Babel;
    if (!Babel || !Babel.transform) {
      console.error("Downloaded Babel but found no transform() on it.");
      process.exit(2);
    }

    const html = fs.readFileSync("/work/static/index.html", "utf8");
    const m = html.match(/<script type="text\/babel"[^>]*>([\s\S]*?)<\/script>/);
    if (!m) {
      console.error("No <script type=text/babel> block found.");
      process.exit(1);
    }
    // Line where the script block starts, so errors map to the real file.
    const offset = html.slice(0, m.index).split("\n").length;

    try {
      // "react" is the preset name the page itself uses, in data-presets.
      const out = Babel.transform(m[1], { presets: ["react"], filename: "index.jsx" });
      if (/(^|\n)\s*import\s/.test(out.code)) {
        console.error("FAILED — the compiled output contains an import statement,");
        console.error("which cannot run in a classic script. Check the JSX runtime.");
        process.exit(1);
      }
      console.log(`OK — JSX compiles cleanly (${out.code.length} bytes generated).`);
    } catch (e) {
      const line = e.loc ? (e.loc.line + offset - 1) : null;
      console.error("FAILED — Babel could not compile the page.");
      console.error("");
      console.error(e.message.replace(/^index\.jsx: /, ""));
      if (line) console.error(`\n-> static/index.html line ${line}`);
      process.exit(1);
    }
  })();
'
echo
echo "Reminder: this proves the page compiles, not that it renders correctly."
echo "Click through the UI after frontend changes."
