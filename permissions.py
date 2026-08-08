"""Role definitions and permission checks.

Roles are strictly ordered, so a permission check is a comparison: each
capability names the minimum role required, and anything higher inherits it.

    Viewer  <  Editor  <  Administrator  <  SuperAdmin

These checks run on the server for every request. The frontend hides controls a
user cannot use, but that is a convenience only — hiding a button is not a
security boundary.
"""

VIEWER = "Viewer"
EDITOR = "Editor"
ADMINISTRATOR = "Administrator"
SUPERADMIN = "SuperAdmin"

ROLES = [VIEWER, EDITOR, ADMINISTRATOR, SUPERADMIN]
_RANK = {role: i for i, role in enumerate(ROLES)}

ROLE_DESCRIPTIONS = {
    VIEWER: "Read-only access to the diagram, catalogs, and schema drill-down.",
    EDITOR: "Everything a Viewer can do, plus creating, updating, and deleting "
            "catalog records and editing schema governance.",
    ADMINISTRATOR: "Everything an Editor can do, plus managing user accounts and roles.",
    SUPERADMIN: "Everything an Administrator can do, plus Preferences and the Audit log.",
}


def rank(role):
    return _RANK.get(role, -1)


def is_valid_role(role):
    return role in _RANK


def at_least(role, minimum):
    """True if ``role`` meets or exceeds ``minimum``."""
    return rank(role) >= rank(minimum)


def can_manage_user(actor_role, target_role):
    """Whether ``actor_role`` may create or modify a user holding ``target_role``.

    Administrators manage Viewers and Editors, but may not create or modify
    SuperAdmins (or other Administrators' elevation to SuperAdmin). Only a
    SuperAdmin can manage SuperAdmins.
    """
    if not at_least(actor_role, ADMINISTRATOR):
        return False
    if actor_role == SUPERADMIN:
        return True
    # Administrator: may not touch SuperAdmin accounts or grant that role.
    return rank(target_role) < rank(SUPERADMIN)


def assignable_roles(actor_role):
    """Roles this actor is allowed to assign."""
    if actor_role == SUPERADMIN:
        return list(ROLES)
    if actor_role == ADMINISTRATOR:
        return [VIEWER, EDITOR, ADMINISTRATOR]
    return []
