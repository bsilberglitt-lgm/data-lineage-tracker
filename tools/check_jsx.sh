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
# mounted read-only and npm installs inside the throwaway container.

set -euo pipefail
cd "$(dirname "$0")/.."

# Matches the @babel/standalone version the page loads from the CDN, so a file
# that compiles here compiles in the browser. Note that @babel/preset-react has
# its own version line and is deliberately NOT pinned to the same number.
BABEL_CORE_VERSION="7.23.5"

echo "Compiling static/index.html with Babel ${BABEL_CORE_VERSION} (via Docker)…"
echo "(first run pulls a small Node image and installs Babel — please wait)"
echo

docker run --rm -v "$PWD":/work:ro node:20-alpine sh -c '
  set -e
  mkdir -p /tmp/babelcheck && cd /tmp/babelcheck

  # Do not silence npm: if this fails, its message is what you need to see.
  if ! npm install --no-fund --no-audit --loglevel=error \
        "@babel/core@'"${BABEL_CORE_VERSION}"'" @babel/preset-react; then
    echo ""
    echo "Could not install Babel in the container (see the npm error above)."
    echo "This is usually a network or registry problem, not a problem with the app."
    exit 2
  fi

  node -e "
    const fs = require(\"fs\");
    const babel = require(\"/tmp/babelcheck/node_modules/@babel/core\");
    const html = fs.readFileSync(\"/work/static/index.html\", \"utf8\");

    const m = html.match(/<script type=\"text\/babel\"[^>]*>([\s\S]*?)<\/script>/);
    if (!m) { console.error(\"No <script type=text/babel> block found.\"); process.exit(1); }

    // Line where the script block starts, so errors map to the real file.
    const offset = html.slice(0, m.index).split(\"\n\").length;

    try {
      const out = babel.transform(m[1], {
        presets: [[\"/tmp/babelcheck/node_modules/@babel/preset-react\", { runtime: \"classic\" }]],
        filename: \"index.jsx\",
        configFile: false,
        babelrc: false,
      });
      if (/(^|\n)\s*import\s/.test(out.code)) {
        console.error(\"FAILED — the compiled output contains an import statement,\");
        console.error(\"which cannot run in a classic script. Check the JSX runtime.\");
        process.exit(1);
      }
      console.log(\"OK — JSX compiles cleanly (\" + out.code.length + \" bytes generated).\");
    } catch (e) {
      const line = e.loc ? (e.loc.line + offset - 1) : null;
      console.error(\"FAILED — Babel could not compile the page.\");
      console.error(\"\");
      console.error(e.message.replace(/^index\.jsx: /, \"\"));
      if (line) console.error(\"\n-> static/index.html line \" + line);
      process.exit(1);
    }
  "
'

echo
echo "Reminder: this proves the page compiles, not that it renders correctly."
echo "Click through the UI after frontend changes."
