"""Safe, stable failure explanations; never serialize exception text or inputs."""

import re

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError


KNOWN = {
    "service dependency incompatible": ("dependency_incompatible", "A required service contract is unavailable or incompatible.", "Check declared dependencies against the loaded service versions and permissions."),
    "schema downgrade is not supported": ("schema_downgrade", "The declared schema version is older than the stored database.", "Restore the current schema version; downgrades require an explicit data migration."),
    "models changed without a schema version increase": ("schema_version_required", "Models changed without increasing the schema version.", "Increase database.schemaVersion and provide migrations for existing data."),
    "database identity changes require explicit data migration": ("database_identity_changed", "The database identity changed.", "Restore the previous database configuration or migrate the data explicitly."),
    "component database is already owned by another component": ("database_owner_conflict", "Another component owns this database schema.", "Configure a separate database or PostgreSQL schema."),
    "database columns do not match declared models": ("schema_columns_mismatch", "Stored columns do not match the declared models.", "Provide a migration that brings the stored schema into agreement with the models."),
}
for reason in ("database column type does not match model", "database nullability does not match model",
               "database string length does not match model", "database primary key does not match model",
               "declared unique constraint is missing from database", "declared composite uniqueness is missing",
               "declared index is missing or incompatible", "declared foreign key is missing"):
    KNOWN[reason] = ("schema_contract_mismatch", reason.capitalize() + ".",
                     "Provide an additive migration that matches the declared schema constraints.")

PATH_FIELDS = set("apiVersion component release api version auth mode secretRef database backend schemaVersion connectionSecret migrations statements models fields type primaryKey generated required unique maxLength resources model path operations handlers response request websocket realtime delivery channels services dependencies jobs limits execution principal verifier access scopes query indexes references".split())

VALIDATION_REASONS = {
    "automatic migrations support additive DDL statements only",
    "string fields require maxLength", "exactly one primary key is required",
    "only bearer requires secretRef", "database and verified modes require declared scopes",
    "only verified mode requires a verifier", "primary key must be a generated integer",
    "include ordered migrations 2 through schemaVersion",
    "postgresql requires connectionSecret; sqlite cannot use one",
    "only Redis realtime requires connectionSecret", "only Redis limits require a connectionSecret",
    "durable events require database and WebSocket resources", "models/resources require database",
    "unknown resource model", "unknown WebSocket resource", "WebSocket route conflicts",
    "permissions must cover exactly the exposed operations in scoped authentication modes",
    "WebSocket permissions must cover exactly its resources in scoped authentication modes",
    "permissions require nonempty, unique, declared scopes", "database and verified handlers require scopes",
    "private policies require individual verified or database principals",
    "owner field must be a string accommodating the stable principal ID",
    "membership policy references unknown fields", "membership key types must match",
    "handler overlaps resource/reserved path", "duplicate handler route",
    "overlapping/reserved resource path", "database authentication requires database",
}
SQL_REASONS = {"42P01": "table_missing", "42703": "column_missing", "42701": "column_exists",
               "42601": "sql_syntax", "23502": "not_null_violation", "23505": "unique_violation",
               "23503": "foreign_key_violation", "42501": "permission_denied"}


def validation_issue(item):
    issue = {"path": [part if isinstance(part, int) else part if part in PATH_FIELDS else "*" for part in item["loc"][:20]],
             "type": item["type"][:80]}
    for reason in VALIDATION_REASONS:
        if item["msg"] == "Value error, " + reason:
            issue["reason"] = reason
    return issue


def explain(error):
    result = {"code": "operation_failed", "message": "The operation failed.",
              "action": "Inspect the component specification and server configuration.",
              "exceptionType": type(error).__name__, "details": {}}
    if isinstance(error, httpx.HTTPStatusError):
        result.update(code="upstream_http_error", message="An upstream request was rejected.",
                      action="Check repository access, credentials, revision and specification path.")
        result["details"]["httpStatus"] = error.response.status_code
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        result.update(code="operation_timeout", message="An operation exceeded its deadline.",
                      action="Check service availability or handler execution time, then retry.")
    if isinstance(error, (FileNotFoundError, PermissionError)):
        result.update(code="local_file_unavailable", message="A required local file is missing or inaccessible.",
                      action="Check mounted configuration, secrets and data-directory permissions.")
    # Only exact matches against server-authored constants may reveal exception messages.
    if isinstance(error, ValueError) and str(error) in KNOWN:
        result["code"], result["message"], result["action"] = KNOWN[str(error)]
    if isinstance(error, ValidationError):
        result.update(code="specification_invalid", message="The specification failed validation.",
                      action="Correct the indicated specification fields and publish a new revision.")
        result["details"]["validation"] = [
            validation_issue(item)
            for item in error.errors(include_input=False, include_context=False, include_url=False)[:20]]
    if isinstance(error, DBAPIError):
        result.update(code="database_operation_failed", message="The database rejected an operation.",
                      action="Check database availability and the declared schema or migration.")
        original = error.orig
        sqlstate = getattr(original, "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
            result["details"]["sqlstate"] = sqlstate
            result["details"]["reason"] = SQL_REASONS.get(sqlstate, "database_rejected_operation")
        sqlite_code = getattr(original, "sqlite_errorname", None)
        if isinstance(sqlite_code, str) and re.fullmatch(r"SQLITE_[A-Z_]{1,60}", sqlite_code):
            result["details"]["sqliteCode"] = sqlite_code
            for prefix, reason in (("no such table:", "table_missing"), ("no such column:", "column_missing"),
                                   ("duplicate column name:", "column_exists"), ("database is locked", "database_busy"),
                                   ("UNIQUE constraint failed:", "unique_violation"), ("NOT NULL constraint failed:", "not_null_violation")):
                if str(original).startswith(prefix):
                    result["details"]["reason"] = reason
    context = getattr(error, "looprd_migration", None)
    if context:
        result.update(code="migration_failed", message="A database migration failed; its transaction was rolled back.",
                      action="Correct the indicated migration statement, then retry reconciliation.")
        result["details"].update(context)
    return result
