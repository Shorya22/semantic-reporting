"""
AST-level read-only SQL guard.

Uses sqlglot to parse the full statement tree and walk every node,
blocking any write or schema-modifying operation even when hidden inside
CTEs or subqueries.  Falls back to a comment-stripping keyword check if
sqlglot cannot parse the given dialect.

Blocked:  INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE,
          GRANT, REVOKE, REPLACE, MERGE, CALL/EXEC
Allowed:  SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA, WITH (read CTEs)
"""

from __future__ import annotations

import re
from typing import Optional

import sqlglot
import sqlglot.expressions as exp

# ---------------------------------------------------------------------------
# AST node types that indicate a write or schema-change operation.
# Walked recursively so writes inside CTEs/subqueries are also caught.
# ---------------------------------------------------------------------------

_BLOCKED_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.TruncateTable,
    exp.Alter,
    exp.AlterColumn,
    exp.Create,       # CREATE TABLE, CREATE VIEW, CREATE INDEX, …
    exp.Grant,
    exp.Revoke,
    exp.Transaction,  # BEGIN/COMMIT/ROLLBACK can wrap write statements
    exp.Command,      # raw DDL that sqlglot can't fully model
    exp.Merge,
)

# ---------------------------------------------------------------------------
# Fallback keyword pattern — applied after stripping SQL comments so that
# comment-wrapped attacks like  SELECT 1 /* UPDATE */ are rejected.
# ---------------------------------------------------------------------------

_BLOCKED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE"
    r"|REPLACE|MERGE|CALL|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

# Catches "SELECT 1; DROP TABLE users" — the separator must be followed by
# a non-whitespace character so trailing semicolons are fine.
_MULTI_STMT = re.compile(r";\s*\S")


def validate_read_only(sql: str, dialect: Optional[str] = None) -> None:
    """
    Raise ``ValueError`` if *sql* contains any write or schema-altering operation.

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

    # ── Multi-statement check ────────────────────────────────────────────────
    # Strip trailing semicolon before checking so "SELECT 1;" is still allowed.
    stripped = sql.strip().rstrip(";")
    if _MULTI_STMT.search(stripped):
        raise ValueError(
            "Multi-statement SQL is not allowed.  "
            "Submit a single SELECT / EXPLAIN / SHOW / DESCRIBE / PRAGMA statement."
        )

    # ── AST walk ─────────────────────────────────────────────────────────────
    try:
        statements = sqlglot.parse(sql, dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE)
        for stmt in statements:
            if stmt is None:
                continue
            for node in stmt.walk():
                if isinstance(node, _BLOCKED_NODE_TYPES):
                    raise ValueError(
                        f"SQL contains a blocked operation ({type(node).__name__}).  "
                        "Only read operations are permitted "
                        "(SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA)."
                    )
    except ValueError:
        raise
    except Exception:
        # sqlglot raised a parse error — fall through to keyword check which
        # is more conservative but never crashes.
        _keyword_check(sql)


def _keyword_check(sql: str) -> None:
    """
    Conservative fallback: strip comments then scan for blocked keywords.

    Stripping comments first resists attacks like:
        SELECT * FROM t --; DROP TABLE t
        SELECT * FROM t /* UPDATE t SET ... */
    """
    # Remove single-line comments (-- …)
    no_line = re.sub(r"--[^\n]*", " ", sql)
    # Remove block comments (/* … */)
    no_block = re.sub(r"/\*[\s\S]*?\*/", " ", no_line)

    match = _BLOCKED_KEYWORDS.search(no_block)
    if match:
        raise ValueError(
            f"SQL contains a blocked keyword ({match.group().upper()}).  "
            "Only read operations are permitted "
            "(SELECT, UNION, EXPLAIN, SHOW, DESCRIBE, PRAGMA)."
        )
