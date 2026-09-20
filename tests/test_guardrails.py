import pytest

from pothole_agent.guardrails import MAX_ROWS, GuardrailError, validate_sql


def test_plain_select_is_wrapped_with_limit():
    out = validate_sql("SELECT COUNT(*) FROM public_cases_fc;")
    assert out.endswith(f"LIMIT {MAX_ROWS}")
    assert "public_cases_fc" in out


def test_cte_names_are_allowed():
    sql = (
        "WITH recent AS (SELECT * FROM public_cases_fc WHERE requested_datetime >= '2026-01-01'), "
        "by_zip AS (SELECT zipcode, COUNT(*) n FROM recent GROUP BY 1) "
        "SELECT * FROM by_zip"
    )
    assert validate_sql(sql)


def test_extract_epoch_from_is_not_mistaken_for_a_table():
    sql = (
        "SELECT AVG(EXTRACT(EPOCH FROM (closed_datetime - requested_datetime)) / 86400) "
        "FROM public_cases_fc WHERE requested_datetime >= '2026-01-01'"
    )
    assert validate_sql(sql)


def test_keywords_inside_string_literals_are_fine():
    assert validate_sql("SELECT 1 FROM public_cases_fc WHERE status_notes = 'please delete; drop'")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "DROP TABLE public_cases_fc",
        "DELETE FROM public_cases_fc",
        "SELECT 1; SELECT 2",
        "SELECT * FROM pg_user",
        "SELECT * FROM public_cases_fc JOIN secrets ON true",
        "SELECT pg_sleep(60) FROM public_cases_fc",
        "SELECT 1 FROM public_cases_fc -- sneaky",
        "SELECT 1 FROM public_cases_fc /* sneaky */",
        "UPDATE public_cases_fc SET status = 'Closed'",
    ],
)
def test_unsafe_queries_are_rejected(bad):
    with pytest.raises(GuardrailError):
        validate_sql(bad)
