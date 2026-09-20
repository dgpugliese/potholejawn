"""SQL guardrails: the agent may only run bounded, read-only queries.

The model writes SQL, so we treat that SQL as untrusted input. Every query
passes through `validate_sql` before it touches the city's API.
"""

from __future__ import annotations

import re

ALLOWED_TABLES = frozenset({"public_cases_fc"})
MAX_ROWS = 200
MAX_QUERY_CHARS = 4000

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|call|execute|reset|listen|notify|lock|"
    r"pg_sleep|pg_read_file|pg_ls_dir|dblink|lo_import|lo_export)\b",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
_CTE_NAME = re.compile(r"(?:\bwith|,)\s+([a-zA-Z_]\w*)\s+as\s*\(", re.IGNORECASE)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
# EXTRACT(EPOCH FROM (...)) and similar use FROM without naming a table.
_FUNCTION_FROM = re.compile(
    r"\b(?:extract|substring|trim|overlay)\s*\([^()]*?\bfrom\b", re.IGNORECASE
)


class GuardrailError(ValueError):
    """Raised when a query violates the safety policy."""


def _strip_literals(sql: str) -> str:
    """Remove string literals so keywords inside them don't trigger rules."""
    return _STRING_LITERAL.sub("''", sql)


def validate_sql(sql: str) -> str:
    """Validate a model-written query and return a bounded version of it.

    Policy:
      * single statement, SELECT or WITH only
      * no comments, no write/admin keywords
      * only allowlisted tables (plus CTE names defined in the query)
      * result size capped at MAX_ROWS by wrapping the query
    """
    if not isinstance(sql, str) or not sql.strip():
        raise GuardrailError("Query is empty.")
    sql = sql.strip().rstrip(";").strip()
    if len(sql) > MAX_QUERY_CHARS:
        raise GuardrailError(f"Query is longer than {MAX_QUERY_CHARS} characters.")

    bare = _strip_literals(sql)
    if ";" in bare:
        raise GuardrailError("Only a single statement is allowed.")
    if "--" in bare or "/*" in bare:
        raise GuardrailError("SQL comments are not allowed.")
    if not re.match(r"^\s*(select|with)\b", bare, re.IGNORECASE):
        raise GuardrailError("Only SELECT queries are allowed.")

    forbidden = _FORBIDDEN.search(bare)
    if forbidden:
        raise GuardrailError(f"Forbidden keyword: {forbidden.group(1).upper()}")

    cte_names = {name.lower() for name in _CTE_NAME.findall(bare)}
    table_scan = _FUNCTION_FROM.sub("(", bare)
    for table in _TABLE_REF.findall(table_scan):
        name = table.lower()
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise GuardrailError(
                f"Table '{table}' is not allowed. Allowed: {sorted(ALLOWED_TABLES)}"
            )

    return f"SELECT * FROM ({sql}) AS guarded_query LIMIT {MAX_ROWS}"
