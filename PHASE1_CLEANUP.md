# Phase 1 — making the repository safe to publish

> **Status: steps 1–3 are done.** The work is committed to your working tree
> but not pushed. Start at step 4. Two things changed from the original plan;
> both are noted in place below.

Working document. Phase 1 ends with the repo **clean but still private**; the
documentation work (Phase 2) comes before it goes public.

Each step says who does it. The ordering is not arbitrary — step 1 has to
happen before the history rewrite, because the rewrite invalidates every local
clone and any work in flight would have to be redone.

---

## Corrections to the earlier audit

Two things I reported were worse than the facts, and one thing I missed.

**The contributor records are not people.** I said `lineage.db` held "2
contributor records with names, titles and org units", which implied personal
data. The two names are `Data Integration Manager` and `ODS Administrator` —
role titles, with email and phone both NULL. There is no personal information
in that database.

**The system names are not sensitive either.** `CONNECT Integration Platform`,
`NEO (State Operational Data Store)`, `Federal Reporting (EDFacts)` and the
rest are the kind of names a state education agency puts in public documents.
EDFacts is a federal program.

**What is actually worth removing** is the detailed schema: 118 tables and
3,304 column names across the `Ceds`, `core`, `Assessment`, `NSC` and
`Reports` schemas. That is a map of Maine's database structure, and it is
reconnaissance material. It lives in `lineage.db`, `neo_schema.json`,
`fedreporting_schema.json` and `FedReporting_Tables.xlsx` — all four removable
by path.

**What I missed:** `seed_db()` in `app.py` is about 306 lines of hard-coded
Maine seed data — the 8 systems, 7 flows and 33 data elements. So removing the
JSON files is not enough on its own: an adopter starting from an empty database
would still have Maine's architecture appear on first run. That is the main
piece of work in step 1.

Because it lives inside `app.py`, it cannot be stripped from history by path
without rewriting that file's entire history. **My recommendation is to leave
history alone on this point** and fix only the current code: system names are
not the sensitive part, and a content-based rewrite of `app.py` would be a much
riskier operation for no real gain. Say so if you disagree — it changes step 4.

---

## Step 1 — Replace the Maine seed with a synthetic one *(done)*

The biggest item, and it has to come first: `tools/harness_setup.py` currently
builds the whole test harness by copying `lineage.db`, so deleting that file
breaks every test suite until this is done.

- [x] Rewrite `seed_db()` with a fictional district — the same *shape* as the
      current seed so the diagram still demonstrates something, with
      role-based rather than Maine-specific names (`Statewide Integration
      Platform` rather than `CONNECT`, and so on).
- [x] Add one small synthetic schema file to replace the two real ones: two or
      three tables with a foreign key between them, enough to show what the
      drill-down does.
- [x] Rewire `tools/harness_setup.py` to build its seed from a generator
      rather than from `lineage.db`.
- [x] Delete `migrate_sqlite_to_postgres.py` — it is a one-time Maine artifact
      and stops working the moment `lineage.db` is gone.
- [x] Fix the README's **Seed Data** section, which currently describes Maine's
      data as though it is what an adopter receives. (A fuller README rewrite
      is Phase 2; this is the minimum needed for it not to be wrong.)
- [x] Verify: app starts against a genuinely empty database, all suites green,
      four static checks green.

**Two things came out of this that were not in the plan.** The generated DDL
was dropping every `REFERENCES` clause — invisible while `lineage.db` supplied
0001's tables, because real Alembic had created them with their constraints.
Generating 0001 lost them, and `t_052` caught it: a free-text system name in a
data element's Source of Truth was stored instead of refused. The generator
emits foreign keys now. And schema seed files are globbed into the harness
rather than listed, after the first attempt left it with no schema to drill
into and `selftest`'s governance checks failing for an unrelated-looking
reason. Both are written up in `CLAUDE.md`.

## Step 2 — Scrub the test fixtures *(done)*

- [x] Replace `maine.gov` with `example.gov` across `tools/tests/_client.py`
      and the eight suites that use it.
- [x] Verify the suites still pass twice against the same database.

Also genericized: the **Allowed email domains** preference's own help text
(which every adopter reads) and a historical note in `CLAUDE.md` that named a
real address.

**Before you run them afterwards:** check **Preferences → Accounts → Allowed
email domains** on your local instance. It defaults to blank, meaning any
domain, and the suites work. If you have set it to `maine.gov`, account
creation will start failing with a clear 400 right after this change.

## Step 3 — Untrack `.DS_Store` *(done, and `lineage.db` too)*

Both were already in `.gitignore` and both were still tracked, because
`.gitignore` has no effect on a file git already tracks. `git rm --cached` has
been run on each, so they are staged for removal and the file stays on your
disk. `lineage.db` was added to this step: leaving it tracked meant the
pre-rewrite commit would still contain it.

One thing still worth doing yourself, so macOS stops reintroducing `.DS_Store`
in every repo you touch:

```bash
git config --global core.excludesfile ~/.gitignore_global
echo ".DS_Store" >> ~/.gitignore_global
```

## Step 4 — Commit and push the cleanup, normally *(you)*

Nothing destructive yet. This is an ordinary commit of steps 1–3.

```bash
git pull                      # in case anything changed on the remote
git add -A
git status                    # read it: nothing unexpected
git commit -F COMMIT_MSG.txt  # Claude will write the message
git push
```

**Stop here and confirm the app works** before rewriting history. Once step 6
runs, rolling back is a restore-from-mirror exercise rather than a `git revert`.

- [ ] Start the app against an empty database and sign in
- [ ] Confirm the synthetic seed appears and looks sensible
- [ ] Run the suites

---

## Step 5 — Archive the repository *(you)*

Do not skip this. A mirror clone captures every ref and is the rollback path
for everything below.

```bash
cd ~
git clone --mirror https://github.com/bsilberglitt-lgm/data-lineage-tracker.git ~/dlt-archive.git
```

Keep `~/dlt-archive.git` somewhere private and off any public service. It is
also where the real `neo_schema.json` and `fedreporting_schema.json` live from
now on, should Maine's instance ever need rebuilding from an empty database —
though the nightly backups already cover that case.

## Step 6 — Rewrite history *(you)*

```bash
brew install git-filter-repo

# Work in a FRESH clone. filter-repo refuses to run on a repo with
# uncommitted work, and you do not want this touching your working copy.
git clone https://github.com/bsilberglitt-lgm/data-lineage-tracker.git ~/dlt-rewrite
cd ~/dlt-rewrite

git filter-repo --invert-paths \
  --path lineage.db \
  --path .DS_Store \
  --path neo_schema.json \
  --path fedreporting_schema.json \
  --path FedReporting_Tables.xlsx \
  --path migrate_sqlite_to_postgres.py
```

Two things to expect, both normal:

- **Every commit hash changes.** Your existing working copy becomes
  incompatible and gets re-cloned in step 8.
- **`filter-repo` deletes the `origin` remote on purpose**, so that you cannot
  force-push by accident before looking at the result.

## Step 7 — Verify before pushing *(you)*

All four of these should return nothing:

```bash
cd ~/dlt-rewrite
git log --all --name-only --pretty=format: | sort -u | grep -i "lineage.db"
git log --all --name-only --pretty=format: | sort -u | grep -i "schema.json"
git log --all --name-only --pretty=format: | sort -u | grep -i "DS_Store"
git log --all --name-only --pretty=format: | sort -u | grep -i "FedReporting"
```

And a positive check — the files that should still be there:

```bash
git ls-tree --name-only HEAD | head -40      # LICENSE, app.py, README.md present
git log --oneline | wc -l                     # commit count, roughly as before
```

## Step 8 — Force-push, then re-clone *(you)*

```bash
cd ~/dlt-rewrite
git remote add origin https://github.com/bsilberglitt-lgm/data-lineage-tracker.git
git push --force --all origin
git push --force --tags origin
```

Then replace your working copy, because its history no longer matches:

```bash
cd "~/Desktop/Desktop Folder/Ben's Folder/Claude Cowork"
mv data-lineage-tracker data-lineage-tracker-OLD
git clone https://github.com/bsilberglitt-lgm/data-lineage-tracker.git data-lineage-tracker
```

**Copy your `.env` across** — it is gitignored, so the fresh clone does not
have it:

```bash
cp data-lineage-tracker-OLD/.env data-lineage-tracker/.env
```

Delete `data-lineage-tracker-OLD` once you have confirmed the new clone works.

## Step 9 — Final checks *(you)*

- [ ] Suites pass in the fresh clone
- [ ] App starts and the synthetic seed appears
- [ ] On github.com, the repo shows MIT in the sidebar and no `lineage.db`
- [ ] Maine's production instance is **untouched** — you have not deployed
      anything, and the schema files were only ever read at startup seeding,
      which skips any system that already has schema rows

---

## One residue worth knowing about

After a force-push, GitHub keeps the old objects server-side. Unreferenced
commits stay reachable by direct SHA URL until GitHub garbage-collects, and the
documented way to force that is to ask GitHub Support.

Because this repository **has been private for its whole life**, nobody
outside has ever been able to clone it or learn a commit SHA, so that residue
is not a real exposure. This is the reason to do the rewrite now rather than
after publishing: the same operation done on a public repo is a much weaker
guarantee.

## What Phase 1 does not include

Going public. The repo is clean at the end of this, but an adopter still has no
install guide, no security contact and no contributing guide — that is Phase 2:

- `INSTALL.md` — the deployment contract, with Render as one example
- `SECURITY.md` — disclosure contact
- `CONTRIBUTING.md` — how to run the suites and the four static checks
- A first-hour guide, and the full README rewrite
- `.github/` — issue and PR templates, and CI running the checks
- Pinned `requirements.txt`, SRI hashes on the three unpkg script tags
