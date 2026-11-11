"""Scenario scoping and access control.

Every question of the form "which records can this request see?" and "may this
person change them?" is answered here, and nowhere else.

That is a deliberate constraint rather than tidiness. Scenario rows and live
rows share the same tables, separated only by ``scenario_id`` (NULL means
live). A single query that forgets the scope would leak somebody's private
plan into the live diagram — the worst failure this application could have. One
helper is something we can test exhaustively; a filter written out by hand in
sixty places is not.

Two rules that are easy to get wrong:

* **NULL means live.** ``scenario_id = 3`` is scenario 3, ``scenario_id IS
  NULL`` is Current Architecture. There is no third meaning, and no query may
  omit the condition.
* **A scenario role is not a system role.** A system Viewer can be an editor
  inside a scenario they were invited to; a system Editor can be a viewer in
  someone else's. The two are resolved together in :func:`access`.
"""

import permissions

# Scenario-level roles, ordered. Deliberately lowercase so they can never be
# confused with the system roles in permissions.py.
S_VIEWER = "viewer"
S_EDITOR = "editor"
S_OWNER = "owner"
SCENARIO_ROLES = [S_VIEWER, S_EDITOR, S_OWNER]
_S_RANK = {r: i for i, r in enumerate(SCENARIO_ROLES)}

# Lifecycle. Only Draft accepts edits; the rest are frozen until someone with
# the right to do so returns the scenario to Draft.
DRAFT = "Draft"
UNDER_REVIEW = "Under review"
APPROVED = "Approved"
ARCHIVED = "Archived"
STATUSES = [DRAFT, UNDER_REVIEW, APPROVED, ARCHIVED]
EDITABLE_STATUSES = [DRAFT]

# How a scenario record differs from the live diagram.
UNCHANGED = "unchanged"
PROPOSED = "proposed"     # new in this scenario
MODIFIED = "modified"     # copied from live, then edited here
REMOVED = "removed"       # retired by this plan; still drawn, struck through
CHANGE_STATES = [UNCHANGED, PROPOSED, MODIFIED, REMOVED]

# Tables holding records that can exist in either place.
SCOPED_TABLES = ["system", "data_flow", "data_element", "contributor",
                 "resource", "schema_snapshot", "schema_table", "schema_column",
                 "flow_element"]

# Of those, the ones a scenario *forks on first edit* rather than copying up
# front. See the module docstring in migration 0005 for why.
COPY_ON_WRITE_TABLES = ["data_element", "contributor", "schema_snapshot",
                        "schema_table", "schema_column"]


class RetireRefused(Exception):
    """Retiring or restoring was not possible. The message is shown to the user."""


def set_retired(conn, table, pk_column, pk_value, scenario_id, retired):
    """Mark a scenario record as retired by this plan, or bring it back.

    Returns the new change state.

    Retiring and restoring live here together, in one function, because they
    are a single piece of reasoning about what a scenario *means* and they used
    to disagree. Two rules:

    * **Something the scenario itself proposed cannot be retired.** There is
      nothing to retire — it does not exist outside the plan — so the honest
      action is to delete it. Retiring it would draw a struck-through box for
      something that was never there.
    * **Restoring returns a record to ``unchanged``, not to ``modified``.**
      Whether the row was edited before being retired is no longer knowable
      once the state has been overwritten, and claiming "modified" when
      nothing differs from live would be a lie in the change set. If it is
      edited again afterwards, ``mark_modified`` will say so truthfully.
    """
    if scenario_id is None:
        raise RetireRefused("Retiring only applies inside a scenario.")
    row = conn.execute(
        f"SELECT change_state FROM {table} "
        f"WHERE {pk_column}=? AND scenario_id=?",
        (pk_value, scenario_id)).fetchone()
    if not row:
        raise RetireRefused("Not found in this scenario.")
    state = row["change_state"]

    if retired:
        if state == PROPOSED:
            raise RetireRefused(
                "This only exists in the scenario, so there is nothing to "
                "retire. Delete it instead.")
        if state == REMOVED:
            return REMOVED                      # already there; not an error
        new_state = REMOVED
    else:
        if state != REMOVED:
            raise RetireRefused("This is not retired, so there is nothing to bring back.")
        new_state = UNCHANGED

    conn.execute(
        f"UPDATE {table} SET change_state=? WHERE {pk_column}=? AND scenario_id=?",
        (new_state, pk_value, scenario_id))
    return new_state


class ScenarioError(Exception):
    """Raised when a scenario rule is broken. Carries an HTTP status.

    404 rather than 403 for anything the user is not a member of: a scenario
    name such as "Replace Vendor X" is itself sensitive, so the app should not
    confirm that it exists.
    """

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class Access:
    """What one user may do in one scenario (or in the live diagram)."""

    def __init__(self, scenario=None, scenario_role=None,
                 can_read=False, can_write=False, can_manage=False,
                 frozen=False):
        self.scenario = scenario
        self.scenario_id = scenario["scenario_id"] if scenario else None
        self.scenario_role = scenario_role
        self.can_read = can_read
        # can_write already accounts for a frozen status, so callers cannot
        # forget to check it separately.
        self.can_write = can_write
        # Membership, rename, status, delete. Owner and SuperAdmin only.
        self.can_manage = can_manage
        self.frozen = frozen

    @property
    def is_live(self):
        return self.scenario_id is None

    def require_read(self):
        if not self.can_read:
            raise ScenarioError("Scenario not found.", 404)

    def require_write(self):
        self.require_read()
        if self.can_write:
            return
        if self.frozen:
            raise ScenarioError(
                f"This scenario is {self.scenario['status']} and cannot be "
                "changed. Return it to Draft first.", 409)
        raise ScenarioError("You do not have permission to change this scenario.", 403)

    def require_manage(self):
        self.require_read()
        if not self.can_manage:
            raise ScenarioError(
                "Only the scenario owner or a SuperAdmin can do that.", 403)


def s_at_least(role, minimum):
    return _S_RANK.get(role, -1) >= _S_RANK.get(minimum, 99)


# ---------------------------------------------------------------------------
# Loading and access
# ---------------------------------------------------------------------------

def get_scenario(conn, scenario_id, include_deleted=False):
    """Load a scenario row, or None. Soft-deleted ones are hidden by default."""
    if scenario_id in (None, "", "live"):
        return None
    try:
        sid = int(scenario_id)
    except (TypeError, ValueError):
        raise ScenarioError("Scenario not found.", 404)
    sql = "SELECT * FROM scenario WHERE scenario_id=?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    row = conn.execute(sql, (sid,)).fetchone()
    return row


def member_role(conn, scenario_id, user_id):
    row = conn.execute(
        "SELECT role FROM scenario_member WHERE scenario_id=? AND user_id=?",
        (scenario_id, user_id)).fetchone()
    return row["role"] if row else None


def access(conn, user, scenario_id, include_deleted=False):
    """Resolve what ``user`` may do in ``scenario_id`` (None = live diagram).

    The live diagram keeps the ordinary system roles. Inside a scenario the
    two role systems combine:

    * **SuperAdmin** — sees and edits every scenario, and can manage any of
      them. Deliberate: someone has to be able to reach an abandoned plan.
    * **Administrator** — can *see* every scenario, but may only edit ones they
      were invited to. Visibility without authority: an administrator can
      answer "what is being planned?" without quietly editing someone's work.
    * **Everyone else** — sees only scenarios they own or were invited to.

    A frozen status removes write access from everybody, SuperAdmin included;
    the way back is to return the scenario to Draft, which is auditable.
    """
    if scenario_id in (None, "", "live"):
        # Live diagram: ordinary system roles, no scenario involved.
        return Access(
            can_read=True,
            can_write=permissions.at_least(user["role"], permissions.EDITOR),
            can_manage=permissions.at_least(user["role"], permissions.ADMINISTRATOR))

    scenario = get_scenario(conn, scenario_id, include_deleted=include_deleted)
    if scenario is None:
        raise ScenarioError("Scenario not found.", 404)

    sys_role = user["role"]
    is_super = sys_role == permissions.SUPERADMIN
    is_admin = permissions.at_least(sys_role, permissions.ADMINISTRATOR)
    role = member_role(conn, scenario["scenario_id"], user["user_id"])

    can_read = bool(role) or is_admin
    if not can_read:
        raise ScenarioError("Scenario not found.", 404)

    frozen = scenario["status"] not in EDITABLE_STATUSES
    can_write = (is_super or s_at_least(role, S_EDITOR)) and not frozen
    can_manage = is_super or role == S_OWNER

    return Access(scenario=scenario, scenario_role=role, can_read=can_read,
                  can_write=can_write, can_manage=can_manage, frozen=frozen)


def visible_scenarios(conn, user, include_deleted=False):
    """Every scenario this user may open, newest first."""
    is_admin = permissions.at_least(user["role"], permissions.ADMINISTRATOR)
    where = [] if include_deleted else ["s.deleted_at IS NULL"]
    params = []
    if not is_admin:
        where.append("(s.owner_user_id=? OR m.user_id IS NOT NULL)")
        params += [user["user_id"]]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(
        f"""SELECT s.*, m.role AS my_role,
                   u.first_name || ' ' || u.last_name AS owner_name,
                   u.email AS owner_email
              FROM scenario s
              LEFT JOIN scenario_member m
                     ON m.scenario_id = s.scenario_id AND m.user_id = ?
              LEFT JOIN app_user u ON u.user_id = s.owner_user_id
              {clause}
             ORDER BY s.created_date DESC""",
        tuple([user["user_id"]] + params)).fetchall()


# ---------------------------------------------------------------------------
# Query scoping — the part that must never be bypassed
# ---------------------------------------------------------------------------

def scope(scenario_id, alias=None):
    """SQL fragment and parameters restricting a query to one view.

    Always use this instead of writing the comparison inline::

        clause, params = scenarios.scope(sid, "s")
        conn.execute(f"SELECT * FROM system s WHERE {clause}", params)

    ``IS NULL`` cannot be expressed as a bound parameter, so a caller building
    the condition by hand tends to write ``scenario_id = ?`` with None, which
    silently matches nothing and produces an empty live diagram.
    """
    col = f"{alias}.scenario_id" if alias else "scenario_id"
    if scenario_id is None:
        return f"{col} IS NULL", ()
    return f"{col} = ?", (scenario_id,)


def visible_scope(scenario_id, alias=None, table=None):
    """Scope for reading, including copy-on-write fall-through.

    Inside a scenario, shared reference data (schemas, data elements,
    contributors) reads through to the live record until someone edits it. So a
    read matches rows belonging to this scenario *plus* live rows this scenario
    has not forked.
    """
    col = f"{alias}.scenario_id" if alias else "scenario_id"
    if scenario_id is None:
        return f"{col} IS NULL", ()
    if table not in COPY_ON_WRITE_TABLES:
        return f"{col} = ?", (scenario_id,)
    # Only copy-on-write tables need the fall-through, and only they are
    # guaranteed to have an entry in _PK.
    pk = f"{alias}.{_PK[table]}" if alias else _PK[table]
    return (
        f"({col} = ? OR ({col} IS NULL AND {pk} NOT IN "
        f"(SELECT origin_id FROM {table} "
        f" WHERE scenario_id = ? AND origin_id IS NOT NULL)))",
        (scenario_id, scenario_id))


_PK = {
    "system": "system_id",
    "data_flow": "flow_id",
    "data_element": "element_id",
    "contributor": "contributor_id",
    "resource": "resource_id",
    "schema_snapshot": "snapshot_id",
    "schema_table": "table_id",
    "schema_column": "column_id",
}


# ---------------------------------------------------------------------------
# Hiding — focus, not deletion
# ---------------------------------------------------------------------------

# Tables carrying ``is_hidden``.
HIDEABLE = {"system": "system_id", "data_flow": "flow_id"}


def hidden_clause(include_hidden, alias=None):
    """Extra condition excluding hidden records, unless showing everything.

    Hiding is a view preference. It never applies to the live diagram, where
    the column exists but is always 0, and it must never be used to narrow the
    data a future analysis runs over — see migration 0005.
    """
    if include_hidden:
        return "", ()
    col = f"{alias}.is_hidden" if alias else "is_hidden"
    return f" AND {col} = 0", ()


def set_hidden(conn, scenario_id, systems=None, flows=None, hidden=True):
    """Hide or show records in bulk, and keep the diagram coherent.

    Hiding a system hides the flows attached to it: a line drawn to nothing is
    worse than no line. Showing a system does *not* automatically bring those
    flows back, because the other end may still be hidden — instead every flow
    whose two endpoints are now both visible is shown.

    Returns how many systems and flows changed.
    """
    changed = {"system": 0, "data_flow": 0}
    value = 1 if hidden else 0

    # Counted with a SELECT rather than read from the cursor: ``rowcount`` is
    # not reported consistently across drivers, and a wrong count here would
    # be written into the audit log as fact.
    for table, pk, key in (("system", "system_id", "system"),
                           ("data_flow", "flow_id", "data_flow")):
        ids = systems if key == "system" else flows
        for rid in (ids or []):
            row = conn.execute(
                f"SELECT is_hidden FROM {table} WHERE {pk}=? AND scenario_id=?",
                (rid, scenario_id)).fetchone()
            if row is None or row["is_hidden"] == value:
                continue
            conn.execute(
                f"UPDATE {table} SET is_hidden=? WHERE {pk}=? AND scenario_id=?",
                (value, rid, scenario_id))
            changed[key] += 1

    if systems:
        changed["data_flow"] += _resync_flows(conn, scenario_id)
    return changed


def _resync_flows(conn, scenario_id):
    """Make every flow's visibility follow its endpoints.

    A flow is drawn only when both of its systems are drawn. This is applied
    after any change to which systems are hidden, so the diagram can never show
    a connection to something that is not there.
    """
    touched = 0
    rows = conn.execute(
        """SELECT f.flow_id, f.is_hidden,
                  s1.is_hidden AS src_hidden, s2.is_hidden AS dst_hidden
             FROM data_flow f
             JOIN system s1 ON s1.system_id = f.source_system_id
             JOIN system s2 ON s2.system_id = f.destination_system_id
            WHERE f.scenario_id = ?""", (scenario_id,)).fetchall()
    for r in rows:
        should_hide = 1 if (r["src_hidden"] or r["dst_hidden"]) else 0
        if r["is_hidden"] != should_hide:
            conn.execute("UPDATE data_flow SET is_hidden=? WHERE flow_id=?",
                         (should_hide, r["flow_id"]))
            touched += 1
    return touched


def reveal_for_flow(conn, scenario_id, *system_ids):
    """Un-hide systems that a flow has just been attached to.

    If someone draws a proposed connection to a system they had tidied away,
    they plainly need to see it again. Silently leaving it hidden would make
    the new flow vanish the moment it was created.
    """
    revealed = []
    for sid in system_ids:
        if not sid:
            continue
        row = conn.execute(
            "SELECT name, is_hidden FROM system WHERE system_id=? AND scenario_id=?",
            (sid, scenario_id)).fetchone()
        if row and row["is_hidden"]:
            conn.execute(
                "UPDATE system SET is_hidden=0 WHERE system_id=? AND scenario_id=?",
                (sid, scenario_id))
            revealed.append(row["name"])
    return revealed


def scoped_id(scenario_id, live_id):
    """Identifier for a scenario's copy of a live record.

    ``SYS-001`` in scenario 3 becomes ``S3-SYS-001``. Prefixing rather than
    renumbering keeps the origin legible in the audit log, guarantees
    uniqueness within the shared table, and makes a leaked scenario row
    obvious the moment it appears somewhere it should not.
    """
    if scenario_id is None:
        return live_id
    return f"S{scenario_id}-{live_id}"


# ---------------------------------------------------------------------------
# Cloning
# ---------------------------------------------------------------------------

# Copied verbatim except for the columns listed in ``_RESET``. Naming the
# columns explicitly would mean editing this file every time a column is added
# to ``system`` — which is exactly the kind of upkeep that gets forgotten and
# silently drops data from a clone.
_RESET = {"scenario_id", "origin_id", "change_state"}


def _columns(conn, table):
    """This table's column names, whether or not it has any rows.

    The empty-table branch was dead code until tasks arrived: `system`,
    `data_flow` and `flow_element` are never empty in any database that gets
    cloned, so nothing had ever asked the cursor. A new installation with no
    tasks reached it and it failed — **a description entry is not reliably a
    tuple.** psycopg exposes `.name`; sqlite3's are plain tuples. Reading the
    attribute first and indexing only as a fallback covers both, which matters
    because the harness is SQLite and production is PostgreSQL.
    """
    row = conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
    if row is not None:
        return list(dict(row).keys())
    cur = conn.execute(f"SELECT * FROM {table} WHERE 1=0")
    return [getattr(d, "name", None) or d[0] for d in cur.description]


def clone_into(conn, scenario_id, source_scenario_id=None):
    """Copy systems, data flows and their element links into a new scenario.

    Reference data (schemas, elements, contributors) is deliberately *not*
    copied — a scenario reads live values and forks a record only when someone
    edits it. See migration 0005.

    Returns a count per table, for the single summary audit event. Bulk work
    gets one event, never one per row: cloning writes tens of rows and
    auditing each would bury real activity.
    """
    counts = {}
    id_map = {}

    for table, pk in (("system", "system_id"), ("data_flow", "flow_id")):
        cols = _columns(conn, table)
        src_where = "scenario_id = ?" if source_scenario_id else "scenario_id IS NULL"
        src_params = (source_scenario_id,) if source_scenario_id else ()
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {src_where}", src_params).fetchall()
        for row in rows:
            row = dict(row)
            old_id = row[pk]
            new_id = scoped_id(scenario_id, base_id(old_id))
            id_map[old_id] = new_id
            row[pk] = new_id
            row["scenario_id"] = scenario_id
            row["origin_id"] = old_id
            # From Current Architecture, everything copied describes what exists
            # today, so it starts unchanged. From another scenario, the copy
            # inherits that scenario's plan: something proposed there is still
            # proposed here, and something being retired is still being retired.
            # Dropping those states would silently turn a plan into a baseline.
            row["change_state"] = (row.get("change_state") or UNCHANGED) \
                if source_scenario_id else UNCHANGED
            names = [c for c in cols if c in row]
            conn.execute(
                f"INSERT INTO {table} ({','.join(names)}) "
                f"VALUES ({','.join('?' for _ in names)})",
                tuple(row[c] for c in names))
        counts[table] = len(rows)

    # Repoint the copied flows at the copied systems. Any endpoint that was not
    # copied (a removed system) leaves the flow pointing at the live id, which
    # would be a cross-view reference, so those flows are dropped.
    orphaned = 0
    for flow in conn.execute("SELECT * FROM data_flow WHERE scenario_id = ?",
                             (scenario_id,)).fetchall():
        src = id_map.get(flow["source_system_id"])
        dst = id_map.get(flow["destination_system_id"])
        if src and dst:
            conn.execute(
                "UPDATE data_flow SET source_system_id=?, destination_system_id=? "
                "WHERE flow_id=?", (src, dst, flow["flow_id"]))
        else:
            conn.execute("DELETE FROM data_flow WHERE flow_id=?", (flow["flow_id"],))
            orphaned += 1
    counts["data_flow"] -= orphaned

    # Element links travel with their flow; the elements themselves stay shared.
    link_cols = [c for c in _columns(conn, "flow_element") if c != "scenario_id"]
    links = 0
    src_where = "scenario_id = ?" if source_scenario_id else "scenario_id IS NULL"
    src_params = (source_scenario_id,) if source_scenario_id else ()
    for link in conn.execute(
            f"SELECT * FROM flow_element WHERE {src_where}", src_params).fetchall():
        new_flow = id_map.get(link["flow_id"])
        if not new_flow:
            continue
        link = dict(link)
        link["flow_id"] = new_flow
        conn.execute(
            f"INSERT INTO flow_element ({','.join(link_cols)}, scenario_id) "
            f"VALUES ({','.join('?' for _ in link_cols)}, ?)",
            tuple(link[c] for c in link_cols) + (scenario_id,))
        links += 1
    counts["flow_element"] = links

    # ---- Tasks ----------------------------------------------------------
    # **Last, and for a reason.** A task is the first record whose parent is
    # another record, so `anchor_id` can only be remapped once both systems
    # and flows have been copied and `id_map` is complete. Copy tasks any
    # earlier and every one of them points at a *live* parent — the
    # cross-view reference the leak tests exist to catch, and a circle drawn
    # on a diagram where its system does not exist.
    #
    # A task whose parent was not copied (a system removed from the plan, or
    # a flow dropped just above for having an uncopied endpoint) is dropped
    # with it, exactly as the flow was.
    task_cols = _columns(conn, "task")
    tasks, dropped = 0, 0
    # Also tracked as a list: `id_map` holds the systems, flows and elements
    # copied earlier too, and the dependency pass below must ask about
    # tasks only.
    copied_tasks = []
    for row in conn.execute(
            f"SELECT * FROM task WHERE {src_where}", src_params).fetchall():
        row = dict(row)
        new_anchor = id_map.get(row["anchor_id"])
        if not new_anchor:
            dropped += 1
            continue
        old_id = row["task_id"]
        new_id = scoped_id(scenario_id, base_id(old_id))
        id_map[old_id] = new_id
        # Tracked separately as well: `id_map` also holds the systems, flows
        # and elements copied earlier, and the dependency pass below must ask
        # about tasks only.
        copied_tasks.append((old_id, new_id))
        row["task_id"] = new_id
        row["anchor_id"] = new_anchor
        row["scenario_id"] = scenario_id
        row["origin_id"] = old_id
        row["change_state"] = (row.get("change_state") or UNCHANGED) \
            if source_scenario_id else UNCHANGED
        names = [c for c in task_cols if c in row]
        conn.execute(
            f"INSERT INTO task ({','.join(names)}) "
            f"VALUES ({','.join('?' for _ in names)})",
            tuple(row[c] for c in names))

        # Steps and assignments are parts of the task rather than records in
        # their own right: they carry no scenario_id and are keyed only on
        # task_id, so copying the task means copying them. Statuses come
        # across unchanged — a scenario describes the same work as it stands,
        # not a fresh occurrence of it, which is what the copy action is for.
        step_cols = [c for c in _columns(conn, "task_step") if c != "step_id"]
        for st in conn.execute(
                "SELECT * FROM task_step WHERE task_id=? ORDER BY sort_order, step_id",
                (old_id,)).fetchall():
            st = dict(st)
            st["task_id"] = new_id
            conn.execute(
                f"INSERT INTO task_step ({','.join(step_cols)}) "
                f"VALUES ({','.join('?' for _ in step_cols)})",
                tuple(st[c] for c in step_cols))
        for tc in conn.execute(
                "SELECT * FROM task_contributor WHERE task_id=?",
                (old_id,)).fetchall():
            tc = dict(tc)
            tc["task_id"] = new_id
            cols = list(tc.keys())
            conn.execute(
                f"INSERT INTO task_contributor ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)})",
                tuple(tc[c] for c in cols))
        tasks += 1

    # Dependencies are copied in a SECOND pass, for the same reason a task's
    # anchor could only be remapped after the systems and flows: a dependency
    # points at another task, and on the first pass the task it points at may
    # not have been given its scenario id yet. One pass would quietly drop
    # every forward reference — half the links, depending on id order, which
    # is the sort of bug that looks like "sometimes it works".
    #
    # A dependency whose target was NOT copied is dropped rather than left
    # pointing across the boundary. That mirrors how a task whose parent was
    # not copied is dropped: a scenario must not reference the live view, and
    # a link to a task the viewer cannot see would show as permanently
    # unfinished and draw the dependent task blocked forever.
    dep_rows = 0
    dep_dropped = 0
    for old_id, new_id in copied_tasks:
        for td in conn.execute(
                "SELECT * FROM task_dependency WHERE task_id=?",
                (old_id,)).fetchall():
            td = dict(td)
            target = id_map.get(td["depends_on_task_id"])
            if not target:
                dep_dropped += 1
                continue
            td["task_id"] = new_id
            td["depends_on_task_id"] = target
            cols = list(td.keys())
            conn.execute(
                f"INSERT INTO task_dependency ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)})",
                tuple(td[c] for c in cols))
            dep_rows += 1

    counts["task"] = tasks
    if dep_rows:
        counts["task_dependency"] = dep_rows
    if dep_dropped:
        counts["task_dependency_dropped"] = dep_dropped
    if dropped:
        counts["task_dropped"] = dropped
    return counts


def schema_source(conn, system_id, scenario_id):
    """Which system's schema rows to read for ``system_id``.

    A scenario copy has no schema of its own until someone changes one, so it
    reads the schema of the live system it came from. Once it has been forked —
    by uploading a new schema, or by editing table or field governance — it has
    its own, and that is what is read from then on.

    Returns ``(source_system_id, is_forked)``.
    """
    if scenario_id is None:
        return system_id, True
    own = conn.execute(
        "SELECT COUNT(*) AS n FROM schema_table WHERE system_id=? AND scenario_id=?",
        (system_id, scenario_id)).fetchone()
    if own["n"]:
        return system_id, True
    row = conn.execute("SELECT origin_id FROM system WHERE system_id=?",
                       (system_id,)).fetchone()
    if row and row["origin_id"]:
        return row["origin_id"], False
    return system_id, True


def fork_schema(conn, scenario_id, scenario_system_id):
    """Give a scenario its own copy of a system's schema, on first change.

    The whole tree is copied at once — snapshot, tables, columns — rather than
    one row at a time. A half-forked schema, with some tables belonging to the
    scenario and the rest still pointing at live, would be incoherent: the next
    read could not tell which set to use, and a governance edit would silently
    land on the live record.

    Does nothing if the schema has already been forked. Returns the number of
    columns copied, for the summary audit event.
    """
    source_id, forked = schema_source(conn, scenario_system_id, scenario_id)
    if forked:
        return 0

    table_map = {}
    tcols = _columns(conn, "schema_table")
    for t in conn.execute(
            "SELECT * FROM schema_table WHERE system_id=? AND scenario_id IS NULL",
            (source_id,)).fetchall():
        row = dict(t)
        old = row["table_id"]
        new = scoped_id(scenario_id, base_id(old))
        table_map[old] = new
        row.update({"table_id": new, "system_id": scenario_system_id,
                    "scenario_id": scenario_id, "origin_id": old,
                    "change_state": UNCHANGED})
        names = [c for c in tcols if c in row]
        conn.execute(f"INSERT INTO schema_table ({','.join(names)}) "
                     f"VALUES ({','.join('?' for _ in names)})",
                     tuple(row[c] for c in names))

    ccols = _columns(conn, "schema_column")
    copied = 0
    for old_tid, new_tid in table_map.items():
        for c in conn.execute(
                "SELECT * FROM schema_column WHERE table_id=? AND scenario_id IS NULL",
                (old_tid,)).fetchall():
            row = dict(c)
            old = row["column_id"]
            row.update({"column_id": scoped_id(scenario_id, base_id(old)),
                        "table_id": new_tid, "scenario_id": scenario_id,
                        "origin_id": old, "change_state": UNCHANGED})
            # Foreign-key targets point at other tables in the same schema, so
            # they have to follow the copy or they would reach back into live.
            if row.get("fk_target_table_id") in table_map:
                row["fk_target_table_id"] = table_map[row["fk_target_table_id"]]
            names = [c2 for c2 in ccols if c2 in row]
            conn.execute(f"INSERT INTO schema_column ({','.join(names)}) "
                         f"VALUES ({','.join('?' for _ in names)})",
                         tuple(row[c2] for c2 in names))
            copied += 1
    return copied


def fork_for_write(conn, table, pk, record_id, scenario_id):
    """Give a scenario its own copy of a shared record, on first edit.

    Returns the identifier to write to. If the record already belongs to this
    scenario, that is returned unchanged. If it is a live record being edited
    from inside a scenario, it is copied first and the *copy* is returned — so
    the live original is never modified.
    """
    if scenario_id is None:
        return record_id
    row = conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (record_id,)).fetchone()
    if row is None:
        return record_id
    if row["scenario_id"] == scenario_id:
        return record_id
    if row["scenario_id"] is not None:
        # Belongs to a different scenario: not ours to touch.
        raise ScenarioError("Record not found.", 404)

    cols = _columns(conn, table)
    new_row = dict(row)
    new_id = scoped_id(scenario_id, base_id(record_id))
    new_row[pk] = new_id
    new_row["scenario_id"] = scenario_id
    new_row["origin_id"] = record_id
    new_row["change_state"] = MODIFIED
    names = [c for c in cols if c in new_row]
    conn.execute(
        f"INSERT INTO {table} ({','.join(names)}) "
        f"VALUES ({','.join('?' for _ in names)})",
        tuple(new_row[c] for c in names))
    return new_id


def base_id(value):
    """Strip a scenario prefix: ``S3-SYS-004`` -> ``SYS-004``.

    Needed anywhere the numeric part of an identifier is parsed. Without it,
    ``re.search(r'\\d+', 'S3-SYS-004')`` finds the *scenario* number 3, and the
    next generated id collides with an existing one.
    """
    if not is_scenario_id(value):
        return value
    return value.split("-", 1)[1]


def is_scenario_id(value):
    """Whether an identifier belongs to some scenario. Used by leak tests."""
    if not isinstance(value, str) or not value.startswith("S"):
        return False
    head, sep, _rest = value.partition("-")
    return bool(sep) and head[1:].isdigit()
