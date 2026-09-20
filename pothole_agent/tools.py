"""Tools the agent can call. Each tool returns a JSON-serialisable dict."""

from __future__ import annotations

import re
from typing import Any

import requests

from .guardrails import MAX_ROWS, validate_sql

CARTO_URL = "https://phl.carto.com/api/v2/sql"
TIMEOUT_SECONDS = 45
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA_NOTES = """\
Table: public_cases_fc  (Philadelphia 311 service requests, 2014 to today, ~6M rows)
Dialect: PostgreSQL.

Columns:
  service_request_id  int
  status              text   'Open' or 'Closed'
  status_notes        text
  service_name        text   category, e.g. 'Street Defect' (potholes), 'Illegal Dumping'
  agency_responsible  text   e.g. 'Streets Department'
  requested_datetime  timestamptz
  updated_datetime    timestamptz
  expected_datetime   timestamptz  the city's own target date
  closed_datetime     timestamptz  NULL while open
  address             text   often NULL
  zipcode             text   often NULL or messy; filter with zipcode ~ '^191[0-9]{2}$'
  lat, lon            float  often NULL

Analysis rules:
  * 'Street Defect' is the pothole category. Use list_categories to confirm names.
  * Exclude service_name = 'Information Request' unless asked (general inquiries).
  * Days to close: EXTRACT(EPOCH FROM (closed_datetime - requested_datetime)) / 86400
  * Averages over closed cases hide the backlog. Always report the share still
    open alongside any time-to-close number, and prefer medians:
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ...)
  * Always filter by requested_datetime; full-table scans are slow.
  * When ranking zip codes, require a minimum count (e.g. HAVING COUNT(*) >= 30).
  * Do not use SQL comments or semicolons; the guardrail rejects them.
"""


def get_schema() -> dict[str, Any]:
    """Describe the dataset so the model can write correct SQL."""
    return {"schema": SCHEMA_NOTES, "max_rows_per_query": MAX_ROWS}


def _execute(sql: str) -> dict[str, Any]:
    response = requests.get(CARTO_URL, params={"q": sql}, timeout=TIMEOUT_SECONDS)
    payload = response.json()
    if response.status_code != 200 or "error" in payload:
        raise RuntimeError(f"Database error: {payload.get('error', response.text[:300])}")
    return payload


def run_sql(query: str, purpose: str = "") -> dict[str, Any]:
    """Run a guarded, read-only query against the city's public API."""
    guarded = validate_sql(query)
    payload = _execute(guarded)
    rows = payload.get("rows", [])
    return {
        "purpose": purpose,
        "row_count": len(rows),
        "truncated": len(rows) >= MAX_ROWS,
        "rows": rows,
    }


def list_categories(since: str = "2026-01-01") -> dict[str, Any]:
    """List complaint categories and volumes since a date (YYYY-MM-DD)."""
    if not isinstance(since, str) or not _DATE.match(since):
        raise ValueError("since must look like YYYY-MM-DD")
    sql = (
        "SELECT service_name, COUNT(*) AS requests FROM public_cases_fc "
        f"WHERE requested_datetime >= '{since}' "
        "GROUP BY service_name ORDER BY requests DESC"
    )
    return run_sql(sql, purpose=f"categories since {since}")


TOOL_SPECS = [
    {
        "name": "get_schema",
        "description": "Get the table schema and analysis rules. Call this first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_categories",
        "description": "List 311 complaint categories with request counts since a date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "YYYY-MM-DD, default 2026-01-01"}
            },
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Run one read-only PostgreSQL SELECT against public_cases_fc. "
            f"Results are capped at {MAX_ROWS} rows, so aggregate in SQL. "
            "If it errors, read the error, fix the query, and try again."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A single SELECT statement."},
                "purpose": {
                    "type": "string",
                    "description": "One sentence: what this query is meant to find out.",
                },
            },
            "required": ["query", "purpose"],
        },
    },
]

TOOL_FUNCTIONS = {
    "get_schema": get_schema,
    "list_categories": list_categories,
    "run_sql": run_sql,
}
