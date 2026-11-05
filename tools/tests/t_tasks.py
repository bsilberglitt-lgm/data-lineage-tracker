"""0.7.0 — tasks: the work attached to a flow or a system.

    python3 tools/tests/t_tasks.py 8900

Four properties this exists to protect, each of which would be a quiet wrong
answer rather than an error if it broke:

**A task's status is its steps' status.** A task with steps does not carry its
own, so it cannot read Completed with three subtasks unfinished underneath. The
rule has an ordering that matters: *any* step started means the task is under
way. Checked on write and on read, because the read path resolves it too so a
row edited directly in the database still reports something true.

**A copy carries everything except the dates and the progress.** That is what
replaced recurrence — a quarterly job is four tasks — so the copy has to bring
the checklist and the people across, and must *not* bring March's dates or
March's ticks, which would be wrong in a way nobody notices until it is
overdue.

**A task belongs to exactly one parent, visible in the view being written.**
A task pointing at a system in another scenario is the cross-view reference the
leak tests exist to catch, and it would draw a circle on a diagram where its
system does not exist.

**Deleting a parent takes its tasks**, and the confirmation counts them first.
"""
import json
import os
import sys


# Credentials, the connection and signing in all come from _client.py, so no
# suite carries a hardcoded password and none of them changes one. See the
# comment at the top of that file for why that matters.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _client import sign_in, session as S, request as rq, BASE as B  # noqa: E402

P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


boss = sign_in()

st, systems = rq(boss, "GET", "/api/systems")
SYS = systems[0]["system_id"]
OTHER_SYS = systems[1]["system_id"]
st, flows = rq(boss, "GET", "/api/flows")
FLOW = flows[0]["flow_id"]

st, people = rq(boss, "GET", "/api/contributors")
PEOPLE = [p["contributor_id"] for p in people][:2]

# --------------------------------------------------------------- task types
print("-- task types are data, not a hardcoded list --")
st, tt = rq(boss, "GET", "/api/task-types")
names = [t["type_name"] for t in tt["types"]]
ck("the four shipped types are present", st == 200 and len(names) == 4, str(names))
ck("Validation is one of them", "Validation" in names, str(names))
ck("the statuses come with them",
   [s["value"] for s in tt["statuses"]] ==
   ["not_started", "in_progress", "completed"], json.dumps(tt.get("statuses")))

st, _ = rq(boss, "POST", "/api/task-types", {"type_name": "Validation"})
ck("a duplicate type is refused", st == 409, str(st))

st, made = rq(boss, "POST", "/api/task-types",
              {"type_name": "Reconciliation",
               "description": "Comparing two sources and explaining the gap."})
ck("a new type can be added", st == 201, str(st))
ck("and sorts last", made["types"][-1]["type_name"] == "Reconciliation",
   made["types"][-1]["type_name"])

# ------------------------------------------------------------------ creating
print("-- creating a task --")
st, _ = rq(boss, "POST", "/api/tasks",
           {"name": "", "task_type": "Validation",
            "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("a nameless task is refused", st == 400, str(st))

st, r = rq(boss, "POST", "/api/tasks",
           {"name": "No type", "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("a task with no type is refused", st == 400, str(st))

st, r = rq(boss, "POST", "/api/tasks",
           {"name": "Bad type", "task_type": "Invented",
            "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("an unknown type is refused by name", st == 400 and "Invented" in json.dumps(r),
   json.dumps(r)[:110])

st, r = rq(boss, "POST", "/api/tasks",
           {"name": "Nowhere", "task_type": "Validation",
            "anchor_kind": "data_element", "anchor_id": "ELE-001"})
ck("a task on something other than a system or flow is refused", st == 400, str(st))

st, r = rq(boss, "POST", "/api/tasks",
           {"name": "Ghost parent", "task_type": "Validation",
            "anchor_kind": "system", "anchor_id": "SYS-999"})
ck("a task on a parent that does not exist is refused", st == 400, str(st))

st, t1 = rq(boss, "POST", "/api/tasks",
            {"name": "Enrollment file checks", "task_type": "Validation",
             "anchor_kind": "data_flow", "anchor_id": FLOW,
             "description": "Row counts and key checks before the load.",
             "expected_start": "2027-01-05", "expected_end": "2027-01-09",
             "pos_main": 0.4, "pos_cross": -18,
             "contributor_ids": PEOPLE})
ck("a task on a flow is created", st == 201, str(st))
T1 = t1.get("task_id", "")
ck("with a generated TSK id", T1.startswith("TSK-"), T1)
ck("it names its parent", t1.get("anchor_name") == flows[0]["name"],
   repr(t1.get("anchor_name")))
ck("both contributors are attached", sorted(t1["contributor_ids"]) == sorted(PEOPLE),
   str(t1["contributor_ids"]))
ck("with no subtasks it has no status at all", t1["status"] is None,
   repr(t1["status"]))
ck("and says the status is its own", t1["status_from_steps"] is False,
   str(t1["status_from_steps"]))
ck("and therefore no label", t1["status_label"] is None,
   repr(t1["status_label"]))

st, t2 = rq(boss, "POST", "/api/tasks",
            {"name": "Quarterly reconciliation", "task_type": "Review and Approval",
             "anchor_kind": "system", "anchor_id": SYS,
             "pos_main": 20, "pos_cross": 20})
ck("a task on a system is created", st == 201, str(st))
T2 = t2["task_id"]
ck("the two ids differ", T1 != T2, f"{T1} {T2}")

# -------------------------------------------------------- status from steps
print("-- the status is the steps' status --")
st, t1 = rq(boss, "PUT", f"/api/tasks/{T1}",
            {"status": "completed",
             "steps": [{"description": "Row count matches the manifest"},
                       {"description": "No duplicate SASIDs",
                        "contributor_id": PEOPLE[0]},
                       {"description": "Grade values are in range"}]})
ck("subtasks are saved in order", [s["description"][:9] for s in t1["steps"]] ==
   ["Row count", "No duplic", "Grade val"], str([s["description"] for s in t1["steps"]]))
ck("a subtask keeps its assignee", t1["steps"][1]["contributor_id"] == PEOPLE[0],
   repr(t1["steps"][1]["contributor_id"]))
# The subtasks were added with no status of their own, so there is nothing to
# derive from and the hand-set Completed is still overruled — to nothing, not
# to "not started". Claiming a status on their behalf is the lie 0019 removed.
ck("Completed set by hand is overruled by subtasks that say nothing",
   t1["status"] is None, repr(t1["status"]))
ck("and the task says the status comes from its steps",
   t1["status_from_steps"] is True, str(t1["status_from_steps"]))

steps = [dict(s) for s in t1["steps"]]
steps[1]["status"] = "completed"
st, t1 = rq(boss, "PUT", f"/api/tasks/{T1}", {"steps": steps})
ck("one subtask finished makes the task In progress",
   t1["status"] == "in_progress", t1["status"])

steps = [dict(s) for s in t1["steps"]]
steps[0]["status"] = "in_progress"
st, t1 = rq(boss, "PUT", f"/api/tasks/{T1}", {"steps": steps})
ck("a subtask merely started also counts as In progress",
   t1["status"] == "in_progress", t1["status"])

for s in steps:
    s["status"] = "completed"
st, t1 = rq(boss, "PUT", f"/api/tasks/{T1}", {"steps": steps})
ck("every subtask finished makes the task Completed",
   t1["status"] == "completed", t1["status"])

# The read path resolves it too, so a stored status cannot disagree.
st, fetched = rq(boss, "GET", f"/api/tasks/{T1}")
ck("the single-task read agrees", fetched["status"] == "completed",
   fetched["status"])
st, catalog = rq(boss, "GET", "/api/tasks")
listed = {t["task_id"]: t for t in catalog}
ck("and so does the catalog", listed[T1]["status"] == "completed",
   listed[T1]["status"])
ck("a task with no subtasks keeps whatever status it was given",
   listed[T2]["status"] is None, repr(listed[T2]["status"]))

st, t2b = rq(boss, "PUT", f"/api/tasks/{T2}", {"status": "in_progress"})
ck("and that status can be set by hand", t2b["status"] == "in_progress",
   t2b["status"])

st, t2b = rq(boss, "PUT", f"/api/tasks/{T2}", {"status": "nonsense"})
ck("an unknown status is ignored rather than stored",
   t2b["status"] == "in_progress", t2b["status"])

# A step can be dropped by sending a shorter list.
st, t1b = rq(boss, "PUT", f"/api/tasks/{T1}",
             {"steps": [{"description": "Row count matches the manifest",
                         "status": "not_started"}]})
ck("subtasks are replaced wholesale", len(t1b["steps"]) == 1, str(len(t1b["steps"])))
ck("and the status follows the survivors", t1b["status"] == "not_started",
   repr(t1b["status"]))

# ------------------------------------------------- a subtask's own dates
print("-- a subtask carries its own date range --")
st, dated = rq(boss, "PUT", f"/api/tasks/{T2}",
               {"steps": [
                   {"description": "First check, the day the file lands",
                    "expected_start": "2027-01-05", "expected_end": "2027-01-05"},
                   {"description": "Sign-off a week later",
                    "expected_start": "2027-01-12", "expected_end": "2027-01-14"},
                   # Either end can be left blank: plenty of subtasks only have
                   # a due date, and a missing end is not the same as the
                   # task's end.
                   {"description": "Due by the end of the month",
                    "expected_end": "2027-01-31"},
                   {"description": "No dates at all"}]})
ck("the dates are stored per subtask",
   [(x["expected_start"], x["expected_end"]) for x in dated["steps"]] ==
   [("2027-01-05", "2027-01-05"), ("2027-01-12", "2027-01-14"),
    (None, "2027-01-31"), (None, None)],
   str([(x["expected_start"], x["expected_end"]) for x in dated["steps"]]))
ck("a blank date is stored as nothing, not an empty string",
   dated["steps"][2]["expected_start"] is None,
   repr(dated["steps"][2]["expected_start"]))

st, again = rq(boss, "GET", f"/api/tasks/{T2}")
ck("and they survive a re-read", again["steps"][1]["expected_end"] == "2027-01-14",
   repr(again["steps"][1]["expected_end"]))

# The task's own pair is the outer window and is independent of them.
st, outer = rq(boss, "PUT", f"/api/tasks/{T2}",
               {"expected_start": "2027-01-01", "expected_end": "2027-01-31"})
ck("the task keeps its own outer window",
   (outer["expected_start"], outer["expected_end"]) == ("2027-01-01", "2027-01-31"),
   f"{outer['expected_start']} {outer['expected_end']}")
ck("without disturbing the subtasks' dates",
   outer["steps"][0]["expected_start"] == "2027-01-05",
   repr(outer["steps"][0]["expected_start"]))

st, copied = rq(boss, "POST", f"/api/tasks/{T2}/copy", {})
ck("a copy drops the subtasks' dates as well as the task's",
   all(not x["expected_start"] and not x["expected_end"] for x in copied["steps"]),
   str([(x["expected_start"], x["expected_end"]) for x in copied["steps"]]))
ck("but keeps the subtasks themselves",
   len(copied["steps"]) == 4, str(len(copied["steps"])))
rq(boss, "DELETE", f"/api/tasks/{copied['task_id']}")
# Put T2 back to no subtasks, so the later checks that rely on its hand-set
# status still describe what they say they do.
rq(boss, "PUT", f"/api/tasks/{T2}", {"steps": [], "status": "in_progress"})

# ---------------------------------------------------------------- the copy
print("-- a copy brings the work, not the dates or the progress --")
st, t1 = rq(boss, "PUT", f"/api/tasks/{T1}",
            {"steps": [{"description": "Row count matches the manifest",
                        "status": "completed", "contributor_id": PEOPLE[0]},
                       {"description": "No duplicate SASIDs",
                        "status": "completed"}],
             "expected_start": "2027-01-05", "expected_end": "2027-01-09"})
ck("the original is Completed before copying", t1["status"] == "completed",
   t1["status"])

st, cp = rq(boss, "POST", f"/api/tasks/{T1}/copy", {})
ck("the copy is created", st == 201, str(st))
CP = cp.get("task_id", "")
ck("with its own id", CP and CP != T1, f"{CP} {T1}")
ck("the name says it is a copy", cp["name"].endswith("(copy)"), cp["name"])
ck("the type comes across", cp["task_type"] == t1["task_type"], cp["task_type"])
ck("the parent comes across",
   (cp["anchor_kind"], cp["anchor_id"]) == ("data_flow", FLOW),
   f"{cp['anchor_kind']} {cp['anchor_id']}")
ck("the people come across", sorted(cp["contributor_ids"]) == sorted(PEOPLE),
   str(cp["contributor_ids"]))
ck("the checklist comes across",
   [s["description"] for s in cp["steps"]] ==
   [s["description"] for s in t1["steps"]], str(len(cp["steps"])))
ck("a subtask's assignee comes across",
   cp["steps"][0]["contributor_id"] == PEOPLE[0],
   repr(cp["steps"][0]["contributor_id"]))
ck("the dates do NOT", not cp["expected_start"] and not cp["expected_end"],
   f"{cp['expected_start']} {cp['expected_end']}")
ck("every subtask's progress resets to nothing",
   all(s["status"] is None for s in cp["steps"]),
   str([s["status"] for s in cp["steps"]]))
ck("so the copy has no status", cp["status"] is None, repr(cp["status"]))
ck("no completed date is carried", not cp["completed_date"],
   repr(cp["completed_date"]))
ck("the position is dropped so the two circles do not overlap",
   cp["pos_main"] is None and cp["pos_cross"] is None,
   f"{cp['pos_main']} {cp['pos_cross']}")
ck("and the original is untouched",
   rq(boss, "GET", f"/api/tasks/{T1}")[1]["status"] == "completed")

st, cp2 = rq(boss, "POST", f"/api/tasks/{T1}/copy",
             {"name": "Enrollment file checks — Q2",
              "expected_start": "2027-04-05"})
ck("a copy can be named and dated in one go",
   cp2["name"].endswith("Q2") and cp2["expected_start"] == "2027-04-05",
   f"{cp2['name']} {cp2['expected_start']}")

st, _ = rq(boss, "POST", "/api/tasks/TSK-404/copy", {})
ck("copying something that is not there is a 404", st == 404, str(st))

# --------------------------------------------------------------- moving it
print("-- moving a task to another parent --")
st, moved = rq(boss, "PUT", f"/api/tasks/{CP}",
               {"anchor_kind": "system", "anchor_id": SYS,
                "pos_main": 0.9, "pos_cross": 40})
ck("a task can be moved onto a system", st == 200 and moved["anchor_kind"] == "system",
   moved["anchor_kind"])
ck("and reports the new parent's name", moved["anchor_name"] == systems[0]["name"],
   repr(moved["anchor_name"]))

st, m2 = rq(boss, "PUT", f"/api/tasks/{CP}",
            {"anchor_kind": "system", "anchor_id": OTHER_SYS})
ck("moving with no position clears the old one, since it meant something else",
   m2["pos_main"] is None and m2["pos_cross"] is None,
   f"{m2['pos_main']} {m2['pos_cross']}")

st, _ = rq(boss, "PUT", f"/api/tasks/{CP}",
           {"anchor_kind": "system", "anchor_id": "SYS-999"})
ck("moving onto a parent that does not exist is refused", st == 400, str(st))

# ------------------------------------------------------ what a delete takes
print("-- deleting a parent takes its tasks, and says so first --")
st, imp = rq(boss, "GET", f"/api/flows/{FLOW}/delete-impact")
counts = {c["label"]: c["count"] for c in imp["counts"]}
ck("the flow's confirmation counts its tasks", counts.get("task") == 2,
   json.dumps(counts))

st, imp = rq(boss, "GET", f"/api/systems/{SYS}/delete-impact")
counts = {c["label"]: c["count"] for c in imp["counts"]}
ck("and the system's does too", counts.get("task") == 1, json.dumps(counts))

st, _ = rq(boss, "DELETE", f"/api/tasks/{CP}")
ck("a task can be deleted", st == 200, str(st))
st, _ = rq(boss, "GET", f"/api/tasks/{CP}")
ck("and is then gone", st == 404, str(st))

# A system no flow touches, so the delete is not refused and the cascade is
# actually exercised rather than skipped.
st, lone = rq(boss, "POST", "/api/systems",
              {"name": "Spare system", "system_type": systems[0]["system_type"],
               "managed_by": "Nobody"})
ck("a spare system is created to delete", st == 201, json.dumps(lone)[:140])
LONE = lone["system_id"]
st, lt = rq(boss, "POST", "/api/tasks",
            {"name": "Task on a doomed system", "task_type": "Validation",
             "anchor_kind": "system", "anchor_id": LONE,
             "contributor_ids": PEOPLE[:1],
             "steps": [{"description": "Something nobody will finish"}]})
LT = lt["task_id"]
st, _ = rq(boss, "DELETE", f"/api/systems/{LONE}")
ck("the lone system is deleted", st == 200, str(st))
st, _ = rq(boss, "GET", f"/api/tasks/{LT}")
ck("its task went with it", st == 404, str(st))
st, catalog = rq(boss, "GET", "/api/tasks")
ck("and the catalog does not list it",
   not any(t["task_id"] == LT for t in catalog), str(len(catalog)))

# ------------------------------------------------------------- permissions
print("-- who may do what --")
VTEMP, VREAL = "Thistle-Quay-52#k", "Bramble-Harbor-71#v"
rq(boss, "POST", "/api/users",
   {"email": "v.task@maine.gov", "first_name": "Val", "last_name": "Reed",
    "role": "Viewer", "password": VTEMP})
viewer = S()
st, vb = rq(viewer, "POST", "/api/auth/login",
            {"email": "v.task@maine.gov", "password": VTEMP})
viewer["t"] = vb.get("csrf_token")
if vb.get("user", {}).get("must_change_password"):
    rq(viewer, "POST", "/api/auth/change-password",
       {"current_password": VTEMP, "new_password": VREAL})

st, _ = rq(viewer, "GET", "/api/tasks")
ck("a Viewer can read the catalog", st == 200, str(st))
st, _ = rq(viewer, "POST", "/api/tasks",
           {"name": "Sneaky", "task_type": "Validation",
            "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("a Viewer cannot create one", st == 403, str(st))
st, _ = rq(viewer, "PUT", f"/api/tasks/{T1}", {"name": "Renamed"})
ck("a Viewer cannot edit one", st == 403, str(st))
st, _ = rq(viewer, "DELETE", f"/api/tasks/{T1}")
ck("a Viewer cannot delete one", st == 403, str(st))

ETEMP, EREAL = "Fathom-Lantern-30#r", "Copper-Meadow-64#j"
rq(boss, "POST", "/api/users",
   {"email": "e.task@maine.gov", "first_name": "Eli", "last_name": "Crane",
    "role": "Editor", "password": ETEMP})
editor = S()
st, eb = rq(editor, "POST", "/api/auth/login",
            {"email": "e.task@maine.gov", "password": ETEMP})
editor["t"] = eb.get("csrf_token")
if eb.get("user", {}).get("must_change_password"):
    rq(editor, "POST", "/api/auth/change-password",
       {"current_password": ETEMP, "new_password": EREAL})

st, et = rq(editor, "POST", "/api/tasks",
            {"name": "Editor's task", "task_type": "Manual Transfer",
             "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("an Editor can create a task", st == 201, str(st))
if st == 201:
    st, _ = rq(editor, "PUT", f"/api/tasks/{et['task_id']}",
               {"steps": [{"description": "Download the extract"}]})
    ck("and edit its subtasks", st == 200, str(st))
st, _ = rq(editor, "POST", "/api/task-types", {"type_name": "Editor type"})
ck("but not add a task type", st == 403, str(st))

# ----------------------------------------------------------- type lifecycle
print("-- renaming and removing a type --")
st, r = rq(boss, "PUT", "/api/task-types/Reconciliation",
           {"type_name": "Reconciliation and sign-off"})
ck("a type can be renamed", st == 200, str(st))
st, r = rq(boss, "DELETE", "/api/task-types/Manual Transfer".replace(" ", "%20"))
ck("a type in use cannot be deleted", st == 409, str(st))
st, r = rq(boss, "DELETE", "/api/task-types/Reconciliation%20and%20sign-off")
ck("an unused one can", st == 200, str(st))

# ---------------------------------------------------------------- scenarios
print("-- a scenario gets its own copy, pointing at its own parents --")
st, scn = rq(boss, "POST", "/api/scenarios",
             {"name": "Task leak check", "start_from": "live"})
SC = scn.get("scenario_id")
ck("the scenario is created", st == 201 and SC, json.dumps(scn)[:140])
ck("the copy report counts tasks", (scn.get("copied") or {}).get("task", 0) > 0,
   json.dumps(scn.get("copied")))

st, live_tasks = rq(boss, "GET", "/api/tasks")
st, scn_tasks = rq(boss, "GET", f"/api/tasks?scenario={SC}")
ck("the scenario has its own copy of every task",
   len(scn_tasks) == len(live_tasks), f"{len(scn_tasks)} vs {len(live_tasks)}")
ck("the copies have scenario-prefixed ids",
   all(t["task_id"].startswith(f"S{SC}-") for t in scn_tasks),
   str([t["task_id"] for t in scn_tasks][:3]))

# The property this whole section exists for: a scenario task must point at a
# scenario parent. Copying tasks before the systems and flows would leave every
# anchor_id on a live record, and the circle would be drawn on a diagram where
# its parent does not exist.
ck("every scenario task points at a scenario parent",
   all(t["anchor_id"].startswith(f"S{SC}-") for t in scn_tasks),
   str([t["anchor_id"] for t in scn_tasks][:3]))
ck("and each one resolves to a parent name",
   all(t["anchor_name"] for t in scn_tasks),
   str([t.get("anchor_name") for t in scn_tasks]))
ck("the copies start as unchanged",
   {t["change_state"] for t in scn_tasks} == {"unchanged"},
   str({t["change_state"] for t in scn_tasks}))

# Steps and assignments are parts of a task, so they come across too.
with_steps = [t for t in scn_tasks if t["steps"]]
ck("a copied task brought its subtasks", bool(with_steps), str(len(scn_tasks)))
ck("and its people",
   any(t["contributor_ids"] for t in scn_tasks),
   str([t["contributor_ids"] for t in scn_tasks]))

ck("no scenario task leaks into the live catalog",
   not [t for t in live_tasks if t["task_id"].startswith(f"S{SC}-")],
   str(len(live_tasks)))

# Editing inside the scenario must not reach the original.
target = scn_tasks[0]
st, edited = rq(boss, "PUT", f"/api/tasks/{target['task_id']}?scenario={SC}",
                {"name": "Edited inside the plan"})
ck("a scenario task can be edited", st == 200, str(st))
ck("and is then marked modified", edited["change_state"] == "modified",
   edited["change_state"])
st, origin = rq(boss, "GET", f"/api/tasks/{target['origin_id']}")
ck("the live original is untouched", origin["name"] != "Edited inside the plan",
   origin["name"])

st, newt = rq(boss, "POST", f"/api/tasks?scenario={SC}",
              {"name": "Invented in the plan", "task_type": "Validation",
               "anchor_kind": "data_flow",
               "anchor_id": scn_tasks[0]["anchor_id"]
               if scn_tasks[0]["anchor_kind"] == "data_flow"
               else [t["anchor_id"] for t in scn_tasks
                     if t["anchor_kind"] == "data_flow"][0],
               "proposed": True})
ck("a task can be proposed inside a scenario", st == 201, json.dumps(newt)[:140])
ck("and reads as proposed", newt.get("change_state") == "proposed",
   str(newt.get("change_state")))

# The checkbox in the flyout sends this, and it was never drawn for a task
# until Ben asked — so the state could be set when creating one and never
# changed afterwards. Toggled both ways here because "it can be turned on"
# says nothing about whether it can be turned off again.
st, unprop = rq(boss, "PUT", f"/api/tasks/{newt['task_id']}?scenario={SC}",
                {"proposed": False})
ck("Proposed can be turned off", unprop.get("change_state") == "unchanged",
   str(unprop.get("change_state")))
st, reprop = rq(boss, "PUT", f"/api/tasks/{newt['task_id']}?scenario={SC}",
                {"proposed": True})
ck("and back on", reprop.get("change_state") == "proposed",
   str(reprop.get("change_state")))
# A record the scenario is retiring must not be quietly un-retired by the
# checkbox — retiring has its own action, the same rule systems and flows use.
st, ret2 = rq(boss, "DELETE",
              f"/api/tasks/{target['task_id']}?scenario={SC}&mode=remove")
st, keep = rq(boss, "PUT", f"/api/tasks/{target['task_id']}?scenario={SC}",
              {"proposed": False})
ck("and it cannot un-retire something the plan is retiring",
   keep.get("change_state") == "removed", str(keep.get("change_state")))
rq(boss, "DELETE", f"/api/tasks/{target['task_id']}?scenario={SC}&mode=restore")

st, r = rq(boss, "PUT", f"/api/tasks/{T2}", {"proposed": True})
ck("Proposed is refused on Current Architecture", st == 400, str(st))

# A live parent is not in the scenario's view, so attaching to one is refused.
st, r = rq(boss, "POST", f"/api/tasks?scenario={SC}",
           {"name": "Cross-view", "task_type": "Validation",
            "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("attaching a scenario task to a live parent is refused", st == 400, str(st))
st, r = rq(boss, "POST", "/api/tasks",
           {"name": "Cross-view the other way", "task_type": "Validation",
            "anchor_kind": "data_flow", "anchor_id": scn_tasks[0]["anchor_id"]
            if scn_tasks[0]["anchor_kind"] == "data_flow" else FLOW})
ck("and a live task cannot be attached to a scenario parent",
   st == 400 or scn_tasks[0]["anchor_kind"] != "data_flow", str(st))

# Retiring, rather than deleting, inside a scenario.
st, ret = rq(boss, "DELETE",
             f"/api/tasks/{target['task_id']}?scenario={SC}&mode=remove")
ck("a copied task can be retired in the plan",
   st == 200 and ret.get("change_state") == "removed", json.dumps(ret)[:120])
st, back = rq(boss, "DELETE",
              f"/api/tasks/{target['task_id']}?scenario={SC}&mode=restore")
ck("and restored to unchanged", back.get("change_state") == "unchanged",
   json.dumps(back)[:120])
st, r = rq(boss, "DELETE", f"/api/tasks/{newt['task_id']}?scenario={SC}&mode=remove")
ck("something the plan proposed cannot be retired, only deleted",
   st == 400, str(st))

# ------------------------------------------------- a status nobody has set
print("-- no status is a real state, not Not started --")
st, blank = rq(boss, "POST", "/api/tasks",
               {"name": "Not assessed yet", "task_type": "Validation",
                "anchor_kind": "data_flow", "anchor_id": FLOW})
BL = blank["task_id"]
ck("a new task starts with no status", blank["status"] is None,
   repr(blank["status"]))
ck("and no label for one", blank["status_label"] is None,
   repr(blank["status_label"]))

st, set_it = rq(boss, "PUT", f"/api/tasks/{BL}", {"status": "in_progress"})
ck("a status can be set", set_it["status"] == "in_progress", set_it["status"])
ck("and reads as words", set_it["status_label"] == "In progress",
   repr(set_it["status_label"]))
st, cleared = rq(boss, "PUT", f"/api/tasks/{BL}", {"status": ""})
ck("and cleared again", cleared["status"] is None, repr(cleared["status"]))
# The exact body `DetailPanel.saveEdit` builds for a task with no subtasks,
# with the status cleared. Ben reported that choosing "Add Status" and saving
# left the old status in place, so the whole payload is driven here rather
# than the one field: a partial request proves the handler works and says
# nothing about what the browser actually sends.
st, full = rq(boss, "PUT", f"/api/tasks/{BL}", {
    "name": "Not assessed yet", "task_type": "Validation",
    "task_type_other": None, "description": None,
    "anchor_kind": "data_flow", "anchor_id": FLOW,
    "expected_start": None, "expected_end": None,
    "completed_date": None, "notes": None,
    "steps": [], "contributor_ids": [], "status": None})
ck("the browser's own payload clears the status", st == 200 and
   full["status"] is None, f"{st} {repr(full.get('status'))}")
st, reread = rq(boss, "GET", f"/api/tasks/{BL}")
ck("and it is still cleared when re-read", reread["status"] is None,
   repr(reread["status"]))
# An empty string is what a <select> hands back, so it has to clear too.
rq(boss, "PUT", f"/api/tasks/{BL}", {"status": "completed"})
st, viaempty = rq(boss, "PUT", f"/api/tasks/{BL}", {"status": ""})
ck("an empty string from a dropdown clears it as well",
   viaempty["status"] is None, repr(viaempty["status"]))

st, junk = rq(boss, "PUT", f"/api/tasks/{BL}", {"status": "nonsense"})
ck("an unrecognised status is ignored, not stored",
   junk["status"] is None, repr(junk["status"]))

# Subtasks with no status say nothing about the task, rather than voting
# "not started" on its behalf.
st, mixed = rq(boss, "PUT", f"/api/tasks/{BL}",
               {"steps": [{"description": "Nobody has said"},
                          {"description": "Nor here"}]})
ck("subtasks that say nothing leave the task with no status",
   mixed["status"] is None, repr(mixed["status"]))
# Once a task has subtasks the status is theirs, including when the answer is
# "nothing" — so the flag says where it comes from, not whether they produced
# a value. A stale hand-set status showing under a field captioned "Determined
# based on subtasks" would contradict its own caption.
ck("and the task still says the status comes from them",
   mixed["status_from_steps"] is True, str(mixed["status_from_steps"]))

# The case that caught a wrong rule: "all completed" has to be asked of EVERY
# subtask, not only the ones with a status. Asking it of the ones that said
# something made one finished subtask out of two report the task Completed.
steps = [dict(x) for x in mixed["steps"]]
steps[0]["status"] = "completed"
st, one = rq(boss, "PUT", f"/api/tasks/{BL}", {"steps": steps})
ck("one subtask finished with the other unset reads In progress",
   one["status"] == "in_progress", repr(one["status"]))
steps = [dict(x) for x in one["steps"]]
steps[1]["status"] = "not_started"
st, mix2 = rq(boss, "PUT", f"/api/tasks/{BL}", {"steps": steps})
ck("one finished and one explicitly not started is still In progress",
   mix2["status"] == "in_progress", repr(mix2["status"]))
steps = [dict(x) for x in one["steps"]]
steps[1]["status"] = "completed"
st, both = rq(boss, "PUT", f"/api/tasks/{BL}", {"steps": steps})
ck("both finished reads Completed", both["status"] == "completed",
   repr(both["status"]))
rq(boss, "DELETE", f"/api/tasks/{BL}")

# ------------------------------------------------------------ "Other" type
print("-- Other is always offered, with a box to say what it is --")
st, tt = rq(boss, "GET", "/api/task-types")
ck("the server names the Other option", tt.get("other") == "Other",
   repr(tt.get("other")))
ck("and does not list it as a configured type",
   "Other" not in [t["type_name"] for t in tt["types"]],
   str([t["type_name"] for t in tt["types"]]))

st, r = rq(boss, "POST", "/api/tasks",
           {"name": "Something unusual", "task_type": "Other",
            "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("Other without a description is refused", st == 400, str(st))

st, oth = rq(boss, "POST", "/api/tasks",
             {"name": "Something unusual", "task_type": "Other",
              "task_type_other": "Annual certification",
              "anchor_kind": "data_flow", "anchor_id": FLOW})
ck("Other with one is accepted", st == 201, f"{st} {json.dumps(oth)[:110]}")
OTH = oth.get("task_id", "")
ck("and reads as the two together",
   oth.get("type_label") == "Other \u2014 Annual certification",
   repr(oth.get("type_label")))

# Switching away from Other must not leave the old wording behind, or it would
# reappear if somebody switched back and look like a value they had typed.
st, moved = rq(boss, "PUT", f"/api/tasks/{OTH}", {"task_type": "Validation"})
ck("changing the type away from Other clears the wording",
   not moved.get("task_type_other"), repr(moved.get("task_type_other")))
ck("and the label is just the type", moved.get("type_label") == "Validation",
   repr(moved.get("type_label")))
st, r = rq(boss, "PUT", f"/api/tasks/{OTH}", {"task_type": "Other"})
ck("switching back to Other needs the wording again", st == 400, str(st))
st, r = rq(boss, "PUT", f"/api/tasks/{OTH}",
           {"task_type": "Other", "task_type_other": "Something else"})
ck("and is accepted with it", st == 200 and
   r.get("type_label") == "Other \u2014 Something else", repr(r.get("type_label")))

# The whole point of keeping it out of task_type: an Editor must not become a
# type author by typing in this box.
st, types_now = rq(boss, "GET", "/api/task-types")
ck("a typed wording does not become a configured type",
   "Annual certification" not in [t["type_name"] for t in types_now["types"]],
   str([t["type_name"] for t in types_now["types"]]))
st, r = rq(boss, "POST", "/api/task-types", {"type_name": "Other"})
ck("and a type literally called Other cannot be added", st == 409, str(st))
rq(boss, "DELETE", f"/api/tasks/{OTH}")

# ------------------------------------------------------- position and docs
print("-- a circle's position is layout, not content --")
st, before_log = rq(boss, "GET", "/api/audit?limit=1")
events_before = before_log.get("total")
st, pos = rq(boss, "PUT", f"/api/tasks/{T1}/position",
             {"pos_main": 0.42, "pos_cross": -18})
ck("a position can be saved", st == 200, str(st))
st, back = rq(boss, "GET", f"/api/tasks/{T1}")
ck("and comes back on the task",
   (back["pos_main"], back["pos_cross"]) == (0.42, -18.0),
   f"{back['pos_main']} {back['pos_cross']}")
# The whole reason this is a separate endpoint: tidying a diagram must not
# read as a change to the plan, and must not fill the audit log.
ck("moving a circle does not mark the record modified",
   back.get("change_state") != "modified", str(back.get("change_state")))
# Counted rather than searched for a field name: the task's *creation* records
# pos_main among its values, so looking for the word finds that and says
# nothing about whether the move was logged.
st, after_log = rq(boss, "GET", "/api/audit?limit=1")
ck("and writes no audit event at all",
   after_log.get("total") == events_before,
   f"{events_before} -> {after_log.get('total')}")

st, cleared = rq(boss, "PUT", f"/api/tasks/{T1}/position",
                 {"pos_main": None, "pos_cross": None})
st, back = rq(boss, "GET", f"/api/tasks/{T1}")
ck("a position can be cleared back to the default slot",
   back["pos_main"] is None and back["pos_cross"] is None,
   f"{back['pos_main']} {back['pos_cross']}")

st, _ = rq(boss, "PUT", "/api/tasks/TSK-404/position", {"pos_main": 0.5})
ck("positioning something that is not there is a 404", st == 404, str(st))
st, _ = rq(viewer, "PUT", f"/api/tasks/{T1}/position", {"pos_main": 0.1})
ck("a Viewer cannot move a circle either", st == 403, str(st))

print("-- documents on a task --")
st, docs = rq(boss, "GET", f"/api/resources/task/{T1}")
ck("a task starts with no documents", st == 200 and docs == [], str(docs)[:80])
st, made = rq(boss, "POST", f"/api/resources/task/{T1}",
              {"label": "Validation checklist", "url": "https://example.gov/checklist",
               "kind": "link"})
ck("a link can be attached", st in (200, 201), f"{st} {json.dumps(made)[:110]}")
st, docs = rq(boss, "GET", f"/api/resources/task/{T1}")
ck("and is listed against the task",
   len(docs) == 1 and docs[0]["label"] == "Validation checklist",
   json.dumps(docs)[:140])
st, _ = rq(boss, "GET", "/api/resources/task/TSK-404")
ck("documents on a task that does not exist is a 404", st == 404, str(st))
# The route pattern names the three parent types, so an unknown one never
# reaches the handler's own check — it simply does not match and falls through.
# Either way it is refused, which is the property worth asserting.
st, _ = rq(boss, "GET", "/api/resources/contributor/CON-001")
ck("and an unsupported parent type is refused", st in (400, 404), str(st))

# ------------------------------------------------- the fifth record type
print("-- a task's fields are in the catalogue, like every other record type --")
st, cat = rq(boss, "GET", "/api/field-catalog")
task_fields = [f for f in cat["fields"] if f["entity"] == "task"]
# Nine since 0.7.0 gained the "Other" wording box. Asserted as a number so a
# field silently dropping out of the catalogue is caught — that failure is
# otherwise invisible: the field just cannot be given a definition.
ck("task fields are catalogued", len(task_fields) == 9, str(len(task_fields)))
cols = {f["column"] for f in task_fields}
ck("including the ones a person fills in",
   {"name", "task_type", "expected_start", "expected_end", "status"} <= cols,
   str(sorted(cols)))
# The parent, the people and the steps are drawn by hand because none is a
# single column: the parent is two columns answered by one choice, the other
# two are lists. Their absence is the design, so it is asserted.
ck("and not the parent, the people or the subtasks",
   not ({"anchor_kind", "anchor_id", "contributor_ids", "steps"} & cols),
   str(sorted(cols)))
ck("Name is marked required", next(
   f["required"] for f in task_fields if f["column"] == "name") is True)

st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": "task", "column": "expected_end",
            "definition": "When this is expected to be finished."})
ck("a definition can be written for a task field", st == 200, str(st))
st, look = rq(boss, "GET", "/api/field-definitions")
ck("and comes back under the label the task views use",
   look["definitions"].get("task|Expected End") ==
   "When this is expected to be finished.",
   str({k: v for k, v in look["definitions"].items() if k.startswith("task|")}))

st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "task", "column": "notes", "hidden": True})
ck("a task field can be hidden", st == 200 and r.get("hidden") is True, str(st))
st, look = rq(boss, "GET", "/api/field-definitions")
ck("and the hidden set names it", "task|notes" in look.get("hidden", []),
   str([h for h in look.get("hidden", []) if h.startswith("task|")]))
st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "task", "column": "name", "hidden": True})
ck("a required task field cannot be hidden", st == 400, str(st))

# ----------------------------------------------------------------- audited
print("-- it is all in the audit log --")
st, log = rq(boss, "GET", "/api/audit?limit=200")
events = log.get("events", log if isinstance(log, list) else [])
text = json.dumps(events)
ck("the task creation is recorded", "task" in text and T1 in text, text[:120])
ck("the copy says where it came from", f"Task copied from {T1}" in text,
   "copy summary missing")

print(f"\n{len(P)} passed, {len(F)} failed")
for f in F:
    print("  FAILED:", f)
sys.exit(1 if F else 0)
