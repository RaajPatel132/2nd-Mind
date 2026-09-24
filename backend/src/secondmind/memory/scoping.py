"""Workspace scoping rules shared by every store (NFR-2.1)."""

# Postgres custom setting the RLS policies read. Set per transaction (SET LOCAL semantics), so
# a pooled connection never carries one request's workspace into another.
WORKSPACE_SETTING = "app.workspace_id"

# Every workspace-owned table enables RLS with exactly this predicate. A missing or empty
# setting makes it NULL, so the comparison is never true: zero rows, not all rows.
RLS_PREDICATE = f"workspace_id = NULLIF(current_setting('{WORKSPACE_SETTING}', true), '')::uuid"

# Group role holding the app's table privileges. Login roles are granted membership; neither
# owns tables nor has BYPASSRLS, so RLS always applies to the app.
APP_GROUP_ROLE = "secondmind_rw"
