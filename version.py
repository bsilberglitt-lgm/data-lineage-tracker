"""Single source of truth for the application version.

Bump this on every release, and add matching release notes (Preferences ->
Releases, or the seeded defaults in app.py). The version is shown in the
bottom-right corner of every page, including the sign-in screen, and drives the
"what's new" modal: a user who hasn't seen this version yet gets the notes on
their next sign-in.

Versioning follows semantic versioning, staying on 0.x while in beta:
    0.MINOR.PATCH   — MINOR for new features, PATCH for fixes only.
"""

VERSION = "0.4.4"
