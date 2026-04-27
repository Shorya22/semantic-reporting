"""
AST-level read-only SQL guard.

Uses sqlglot to parse the full statement tree and walk every node,
blocking any write, schema-changing, side-effecting, or potentially
filesystem-touching operation — even when hidden inside CTEs, subqueries,
or sqlglot's catch-all ``Command`` node.

Falls back to a comment-stripping keyword regex when sqlglot can't parse
the input dialect cleanly.

Layers in this file
-------------------
1. **Multi-statement check** — rejects ``;\\S`` so stacked statements
   like ``SELECT 1; DROP TABLE x`` cannot slip through.
2. **AST blocklist** — every parsed node is checked against a tuple of
   write/DDL/transactional/lock/attach types pulled from sqlglot at
   import time (``getattr(exp, …, None)`` so the file still loads on
   older sqlglot versions).
3. **Dangerous-function regex** — even within a SELECT, certain
   functions have side effects (``pg_read_file``, ``pg_write_file``,
   ``lo_import``, ``lo_export``, ``load_extension``, ``copy …``,
   ``into outfile``, ``attach database``). Caught here as a belt-and-
   braces second pass on the raw SQL text.
4. **Keyword fallback** — strips comments, then regex-scans for blocked
   verbs. Used only when sqlglot raises a parse error.

Allowed: SELECT, UNION/INTERSECT/EXCEPT, EXPLAIN, DESCRIBE/DESC, SHOW,
read-only PRAGMA queries, WITH (read CTEs).
"""

from __future__ import annotations

import re
from typing import Optional

import sqlglot
import sqlglot.expressions as exp


# ---------------------------------------------------------------------------
# AST node blocklist.
#
# We pull node classes by name with a default of ``None`` so missing types
# in older sqlglot versions don't break import. The final tuple filters
# out the Nones.
# ---------------------------------------------------------------------------

def _resolve(*names: str) -> tuple:
    out = []
    for n in names:
        cls = getattr(exp, n, None)
        if cls is not None:
            out.append(cls)
    return tuple(out)


_BLOCKED_NODE_TYPES: tuple = _resolve(
    # Data writes
    "Insert", "Update", "Delete", "Merge", "Replace",
    # Schema / DDL
    "Drop", "TruncateTable", "Truncate", "Alter", "AlterColumn",
    "Create", "Rename", "Comment", "Refresh",
    # Privileges
    "Grant", "Revoke",
    # Transactions / locks (can wrap writes or block other readers)
    "Transaction", "Commit", "Rollback", "Savepoint", "Lock", "Unlock",
    # Session-altering / dangerous configuration
    "Use", "Set", "SetItem",
    # Filesystem / cross-database I/O
    "AttachDatabase", "Attach", "Detach", "DetachDatabase",
    "Copy", "Load", "LoadData", "Export", "Import",
    # Maintenance
    "Vacuum", "Analyze", "Reindex", "Optimize",
    # Fallback for anything sqlglot didn't model fully
    "Command",
)


# ---------------------------------------------------------------------------
# Dangerous-function / keyword-pattern regexes (run on the raw SQL text).
# Catch SELECTs that *look* read-only but call functions or syntax with
# side effects — e.g. PostgreSQL's ``pg_read_file`` or MySQL's
# ``INTO OUTFILE``.
# ---------------------------------------------------------------------------

# Whole-word verbs that should never appear in read-only SQL.
_BLOCKED_KEYWORDS = re.compile(
    r"\b(?:"
    r"INSERT|UPDATE|DELETE|REPLACE|MERGE"
    r"|DROP|TRUNCATE|ALTER|CREATE|RENAME|COMMENT"
    r"|GRANT|REVOKE"
    r"|EXEC|EXECUTE|CALL"
    r"|ATTACH|DETACH"
    r"|VACUUM|REINDEX|ANALYZE|OPTIMIZE"
    r"|LOCK|UNLOCK"
    r"|BEGIN|COMMIT|ROLLBACK|SAVEPOINT"
    r")\b",
    re.IGNORECASE,
)

# Phrase-level patterns that span multiple words.
_BLOCKED_PHRASES = re.compile(
    r"(?:"
    r"\bINTO\s+OUTFILE\b"          # MySQL: SELECT … INTO OUTFILE '/etc/passwd'
    r"|\bINTO\s+DUMPFILE\b"        # MySQL: dump to file
    r"|\bLOAD\s+DATA\b"            # MySQL: LOAD DATA INFILE …
    r"|\bCOPY\b\s+(?:[a-zA-Z_\"][^\s]*\s+)?(?:FROM|TO)\b"  # Postgres COPY
    r"|\bSELECT\s+.*\bINTO\s+\b(?!OUTFILE\b|DUMPFILE\b)\w+"  # SELECT … INTO new_table
    r"|\bATTACH\s+(?:DATABASE\b|['\"\w])"   # SQLite ATTACH DATABASE
    r"|\bDETACH\s+(?:DATABASE\b|\w)"        # SQLite DETACH DATABASE
    r")",
    re.IGNORECASE | re.DOTALL,
)

# When sqlglot can't fully model a statement it falls back to ``Command``.
# We can't reject every Command (EXPLAIN/SHOW/DESC/PRAGMA all parse as
# Command in some dialects) — so we inspect the leading verb instead.
_ALLOWED_COMMAND_VERBS = re.compile(
    r"^\s*(?:EXPLAIN|SHOW|DESC|DESCRIBE|PRAGMA|WITH)\b",
    re.IGNORECASE,
)

# Dangerous functions — read-only by API contract but with side effects.
_DANGEROUS_FUNCTIONS = re.compile(
    r"\b(?:"
    r"pg_read_file|pg_read_binary_file|pg_write_file|pg_ls_dir|pg_stat_file"
    r"|lo_import|lo_export"
    r"|pg_terminate_backend|pg_cancel_backend|pg_reload_conf"
    r"|pg_create_(?:physical|logical)_replication_slot|pg_drop_replication_slot"
    r"|set_config"
    r"|load_extension|sqlite_load_extension"
    r"|sys_exec|sys_eval"   # SQLite/MySQL UDF abuse
    r"|xp_cmdshell"         # SQL Server
    r"|openrowset|opendatasource"  # SQL Server external data
    r")\s*\(",
    re.IGNORECASE,
)

# Catches "SELECT 1; DROP TABLE users" — the separator must be followed by
# a non-whitespace character so trailing semicolons are fine.
_MULTI_STMT = re.compile(r";\s*\S")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_read_only(sql: str, dialect: Optional[str] = None) -> None:
    """
    Raise ``ValueError`` if *sql* contains any write, schema-altering, or
    side-effecting operation.

    Parameters
    ----------
    sql:
        Complete SQL string to validate.
    dialect:
        Optional sqlglot dialect hint (``"sqlite"``, ``"postgres"``).
        When ``None`` sqlglot uses its generic dialect.

    Raises
    ------
    ValueError
        With a human-readable explanation of the blocked operation.
    """
    if not sql or not sql.strip():
        return

    # ── 1. Multi-statement check ─────────────────────────────────────────────
    stripped = sql.strip().rstrip(";")
    if _MULTI_STMT.search(stripped):
        raise ValueError(
            "Multi-statement SQL is not allowed. Submit a single read-only "
            "statement (SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA)."
        )

    # ── 2. Dangerous-function / phrase check on raw text ────────────────────
    # Run before the AST walk so payloads that sqlglot rewrites into the
    # generic ``Command`` node (which we'd report by its node name) get a
    # more specific error message.
    if _DANGEROUS_FUNCTIONS.search(sql):
        raise ValueError(
            "SQL calls a side-effecting function that is not permitted "
            "(file I/O, server-side process control, or extension loading)."
        )
    if _BLOCKED_PHRASES.search(sql):
        raise ValueError(
            "SQL contains a blocked phrase (filesystem export, COPY, LOAD DATA, "
            "or SELECT … INTO). Only in-database read operations are permitted."
        )

    # ── 3. AST walk ─────────────────────────────────────────────────────────
    Command = getattr(exp, "Command", None)
    try:
        statements = sqlglot.parse(sql, dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE)
        for stmt in statements:
            if stmt is None:
                continue
            for node in stmt.walk():
                if not _BLOCKED_NODE_TYPES or not isinstance(node, _BLOCKED_NODE_TYPES):
                    continue

                # Special case: ``Command`` is sqlglot's catch-all for
                # statements it can't fully model. EXPLAIN / SHOW / DESC /
                # PRAGMA in many dialects fall here even though they're
                # read-only. Inspect the raw text of the original SQL to
                # decide whether to block.
                if Command is not None and isinstance(node, Command):
                    if _ALLOWED_COMMAND_VERBS.match(sql):
                        continue  # safe verb — keep walking

                raise ValueError(
                    f"SQL contains a blocked operation ({type(node).__name__}). "
                    "Only read operations are permitted "
                    "(SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA)."
                )
    except ValueError:
        raise
    except Exception:
        # sqlglot raised a parse error — fall through to the keyword check
        # which is more conservative but never crashes.
        _keyword_check(sql)


def _keyword_check(sql: str) -> None:
    """
    Conservative fallback: strip comments then scan for blocked verbs and
    phrases.

    Stripping comments first resists tricks like:
        SELECT * FROM t --; DROP TABLE t
        SELECT * FROM t /* UPDATE t SET ... */
    """
    no_line = re.sub(r"--[^\n]*", " ", sql)
    no_block = re.sub(r"/\*[\s\S]*?\*/", " ", no_line)

    if _DANGEROUS_FUNCTIONS.search(no_block):
        raise ValueError(
            "SQL calls a side-effecting function that is not permitted."
        )
    if _BLOCKED_PHRASES.search(no_block):
        raise ValueError(
            "SQL contains a blocked phrase (filesystem export, COPY, LOAD DATA, "
            "or SELECT … INTO)."
        )

    match = _BLOCKED_KEYWORDS.search(no_block)
    if match:
        raise ValueError(
            f"SQL contains a blocked keyword ({match.group().upper()}). "
            "Only read operations are permitted "
            "(SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA)."
        )
