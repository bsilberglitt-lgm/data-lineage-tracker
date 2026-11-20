#!/usr/bin/env bash
# Run the diagram-geometry suites in Docker, so Node is not needed locally.
#
#   ./tools/run_geometry_tests.sh
#
# `node tools/tests/t_align.js` works wherever Node happens to be installed,
# and on a Mac with no Node it just says "command not found" — which is what
# stopped Ben at checklist step 1b-iii. Docker is already required for local
# development and for tools/check_jsx.sh, so this needs nothing new: the
# project is mounted read-only and the container is thrown away.
#
# These suites are plain Node with no dependencies, so unlike check_jsx.sh
# these need no network at all: nothing is downloaded and there is no registry
# to go wrong. check_jsx.sh fetches one file; this fetches none.
#
# Node 22, not 20: everything before 22.x is end-of-life and was not patched
# for the October 2026 TLS and Brotli issues. This container is a local test
# tool that is never deployed and does no networking, so it was never exposed —
# but there is no reason to keep reaching for an unpatched runtime.

set -euo pipefail
cd "$(dirname "$0")/.."

# Every geometry suite. Listed rather than globbed so a new one has to be added
# here deliberately — a suite that silently stops being run is worse than one
# that does not exist, because the checklist claims it passed.
SUITES=(
  tools/tests/t_align.js      # alignment guides, 45/90 snapping, export extent
  tools/tests/t_taskpos.js    # where a task's circle sits, and dragging it
  tools/tests/t_labelpos.js   # where a data flow's label sits
  tools/tests/t_autoname.js   # the auto-filled flow name
  tools/tests/t_tasksignal.js  # which color a task's circle takes
  tools/tests/t_scopedurl.js  # which view an API request is about
)

echo "Running the geometry suites in Docker (no local Node needed)…"
echo "(first run pulls a small Node image — please wait)"
echo

# One container for all of them: pulling the image is the slow part, and a
# single run also means one exit status to read.
docker run --rm -v "$PWD":/work:ro -w /work node:22-alpine sh -c '
  set -e
  fail=0
  for suite in '"${SUITES[*]}"'; do
    if [ ! -f "$suite" ]; then
      echo "MISSING  $suite"
      fail=1
      continue
    fi
    echo "== $suite"
    # Not `set -e` sensitive: every suite is run even when an earlier one
    # fails, because knowing which ones broke is the point.
    if node "$suite"; then :; else fail=1; fi
    echo
  done
  exit $fail
'

echo "All geometry suites passed."
echo
echo "Reminder: these prove the arithmetic, not that dragging FEELS right."
echo "That is the click-through."
