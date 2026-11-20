from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llm_finops_gateway.auth import KeyRing, generate_key
from llm_finops_gateway.config import ProviderKind, Settings
from llm_finops_gateway.ledger import CostEntry, Ledger
from llm_finops_gateway.main import create_app
from llm_finops_gateway.policy import PolicySet
from llm_finops_gateway.pricing import PriceBook
from tests.conftest import FakeProvider

RAW_PLATFORM, HASH_PLATFORM = generate_key()
RAW_FINOPS, HASH_FINOPS = generate_key()


def _seed(ledger: Ledger) -> None:
    for i, (team, cost) in enumerate((("platform", "0.5"), ("research", "0.25"))):
        ledger.record(
            CostEntry(
                request_id=f"seed-{i}",
                team=team,
                project="p",
                use_case="u",
                provider="fake",
                model="priced",
                input_tokens=10,
                output_tokens=5,
                cost_usd=Decimal(cost),
                price_book_version="test-1",
            )
        )


def _app(
    mode: str,
    pricing_path: Path,
    price_book: PriceBook,
    ledger: Ledger,
    tmp_path: Path,
    policies: PolicySet | None = None,
) -> TestClient:
    keys = tmp_path / "keys.yaml"
    keys.write_text(
        "version: t\nkeys:\n"
        f"  - {{id: platform-key, team: platform, sha256: {HASH_PLATFORM}}}\n"
        f"  - {{id: finops-key, team: finops, sha256: {HASH_FINOPS}, role: finops}}\n"
    )
    settings = Settings(
        provider=ProviderKind.OLLAMA,
        auth_mode=mode,  # type: ignore[arg-type]
        pricing_path=pricing_path,
        keys_path=keys,
        database_url="sqlite:///:memory:",
        report_rates={"EUR": 0.88},
    )
    app = create_app(
        settings,
        price_book=price_book,
        policies=policies or PolicySet.empty(),
        keyring=KeyRing.from_yaml(keys),
        ledger=ledger,
        provider=FakeProvider(),
    )
    return TestClient(app)


@pytest.fixture
def open_client(
    pricing_path: Path, price_book: PriceBook, ledger: Ledger, tmp_path: Path
) -> Iterator[TestClient]:
    _seed(ledger)
    with _app("headers", pricing_path, price_book, ledger, tmp_path) as c:
        yield c


@pytest.fixture
def keyed_client(
    pricing_path: Path, price_book: PriceBook, ledger: Ledger, tmp_path: Path
) -> Iterator[TestClient]:
    _seed(ledger)
    with _app("api_key", pricing_path, price_book, ledger, tmp_path) as c:
        yield c


def test_html_report_lists_budgets_and_chargeback(open_client: TestClient) -> None:
    r = open_client.get("/finops")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "platform" in r.text and "research" in r.text and 'data-amt="0.75"' in r.text
    assert "all teams" in r.text and ">test-1</a>" in r.text


def test_html_report_has_selectors_policy_and_price_views(open_client: TestClient) -> None:
    text = open_client.get("/finops").text
    # team selector: a dropdown with "all" plus one checkbox per team
    assert 'id="team-btn"' in text and 'id="team-menu"' in text and 'id="team-all"' in text
    assert 'class="team" value="platform"' in text and 'class="team" value="research"' in text
    # currency selector: the book currency and the configured display currencies
    assert '<option value="USD">USD</option>' in text and '<option value="EUR">EUR</option>' in text
    assert '"EUR": 0.88' in text
    # policy view: defaults row and the raw file; price book view with the priced model
    assert '<details id="policy">' in text and "(defaults)" in text
    assert '<details id="pricebook">' in text and "<td>priced</td>" in text
    assert 'data-amt="1.25"' in text  # cached_input price of "priced"
    # sortable headers and per-table filters
    assert 'class="sortable" id="chargeback"' in text and 'data-table="chargeback"' in text


def test_team_key_sees_only_its_own_team_in_selectors(keyed_client: TestClient) -> None:
    text = keyed_client.get("/finops", headers={"Authorization": f"Bearer {RAW_PLATFORM}"}).text
    assert 'class="team" value="platform"' in text and 'value="research"' not in text


def test_csv_summary(open_client: TestClient) -> None:
    r = open_client.get("/v1/finops/summary.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "team,project,model,calls,input_tokens,output_tokens,cost_usd"
    assert "platform,p,priced,1,10,5,0.5" in lines and "research,p,priced,1,10,5,0.25" in lines


def test_team_key_sees_only_its_own_team(keyed_client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {RAW_PLATFORM}"}
    summary = keyed_client.get("/v1/finops/summary", headers=auth).json()
    assert {r["team"] for r in summary["rows"]} == {"platform"}
    assert summary["total_cost_usd"] == "0.5"
    budgets = keyed_client.get("/v1/finops/budgets", headers=auth).json()
    assert [t["team"] for t in budgets["teams"]] == ["platform"]
    other = keyed_client.get("/v1/finops/summary", headers={**auth, "X-Team": "research"})
    assert other.status_code == 403 and other.json()["detail"]["error"] == "team_mismatch"


def test_finops_key_sees_every_team_and_can_filter(keyed_client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {RAW_FINOPS}"}
    summary = keyed_client.get("/v1/finops/summary", headers=auth).json()
    assert {r["team"] for r in summary["rows"]} == {"platform", "research"}
    filtered = keyed_client.get("/v1/finops/summary?team=research", headers=auth).json()
    assert [r["team"] for r in filtered["rows"]] == ["research"]
    page = keyed_client.get("/finops", headers=auth)
    assert page.status_code == 200 and "research" in page.text and "key finops-key" in page.text


def test_reports_need_a_key_in_api_key_mode(keyed_client: TestClient) -> None:
    assert keyed_client.get("/v1/finops/summary").status_code == 401
    assert keyed_client.get("/v1/finops/summary.csv").status_code == 401
    page = keyed_client.get("/finops")
    assert page.status_code == 200 and '<form method="post" action="/finops/login"' in page.text
    assert "platform" not in page.text


def test_login_form_sets_cookie_and_scopes_the_page(keyed_client: TestClient) -> None:
    r = keyed_client.post("/finops/login", data={"key": RAW_PLATFORM}, follow_redirects=False)
    assert r.status_code == 303 and "lfg_report_key" in r.headers["set-cookie"]
    assert "httponly" in r.headers["set-cookie"].lower()
    page = keyed_client.get("/finops")
    assert page.status_code == 200
    assert "team <b>platform</b>" in page.text and "research" not in page.text
    bad = keyed_client.post("/finops/login", data={"key": "lfg_wrong"}, follow_redirects=False)
    assert bad.status_code == 200 and "invalid api key" in bad.text
    keyed_client.get("/finops/logout", follow_redirects=False)
    assert '<form method="post"' in keyed_client.get("/finops").text


def test_budget_bar_and_percentage_stay_on_one_line() -> None:
    from llm_finops_gateway.models import BudgetEnvelope, TeamBudgetRow
    from llm_finops_gateway.report import _STYLE, _budget_cells

    budget = BudgetEnvelope(
        limit_usd="0.02",
        spent_usd="0.0105253",
        reserved_usd="0",
        used_pct="52.6",
        remaining_usd="0.0094747",
        level="warn",
        enforcement="block",
        warn_at_pct="50",
        block_at_pct="100",
    )
    row = TeamBudgetRow(
        team="t", allowed_models=["gpt-4.1-mini", "claude-haiku-5-5"], budget=budget
    )
    # a long model list next to it used to push the "%" onto a second line
    assert '<td class="used" data-v="52.6">' in _budget_cells(row.budget)
    assert "td.used { white-space: nowrap; }" in _STYLE


@pytest.mark.parametrize("scope", ["all", "platform", "filtered"])
def test_report_includes_nested_budget_scopes_and_reservation_cap(
    scope: str, pricing_path: Path, price_book: PriceBook, ledger: Ledger, tmp_path: Path
) -> None:
    policies_path = tmp_path / "report-policies.yaml"
    policies_path.write_text(
        "defaults:\n  allowed_models: ['*']\nteams:\n"
        "  platform:\n"
        "    budget: {monthly_usd: 5, reserve_output_tokens: 100, "
        "reserve_output_tokens_max: 8000}\n"
        "    model_budgets:\n      'team<model>': {monthly_usd: 2, enforcement: warn}\n"
        "    projects:\n      'chat<project>':\n"
        "        budget: {monthly_usd: 3}\n"
        "        model_budgets:\n          'project<model>': {monthly_usd: 1}\n"
        "      unbudgeted: {}\n"
        "  research:\n    budget: {monthly_usd: 9}\n"
    )
    with _app(
        "api_key", pricing_path, price_book, ledger, tmp_path, PolicySet.from_yaml(policies_path)
    ) as client:
        auth = {"Authorization": f"Bearer {RAW_PLATFORM if scope == 'platform' else RAW_FINOPS}"}
        url = "/finops?team=platform" if scope == "filtered" else "/finops"
        text = client.get(url, headers=auth).text
        table = text.split('id="budgets"', 1)[1].split("</table>", 1)[0]
        rows = re.findall(r"<tr data-team=.*?</tr>", table)
        assert len(rows) == (6 if scope == "all" else 5)
        assert "team&lt;model&gt;" in table and "project&lt;model&gt;" in table
        assert "chat&lt;project&gt;" in table and "<model>" not in table
        assert 'data-amt="5"' in table and 'data-amt="3"' in table
        assert 'data-amt="2"' in table and 'data-amt="1"' in table
        assert "unbudgeted" in table and "no budget" in table
        assert "never blocked" in table
        assert ("research" in table) == (scope == "all")
        policy = text.split('id="policy-table"', 1)[1].split("</table>", 1)[0]
        assert "reserve tokens max" in policy and ">8000</td>" in policy
        assert ">100</td>" in policy and ">-</td>" in policy
        csv = client.get("/v1/finops/summary.csv", headers=auth)
        assert (
            csv.text.splitlines()[0]
            == "team,project,model,calls,input_tokens,output_tokens,cost_usd"
        )


def test_budget_rows_keep_their_columns_and_name_a_model_once() -> None:
    from llm_finops_gateway.models import BudgetEnvelope, ProjectBudgetRow, TeamBudgetRow
    from llm_finops_gateway.report import _budget_rows

    budget = BudgetEnvelope(
        limit_usd="2",
        spent_usd="1",
        reserved_usd="0",
        used_pct="50.0",
        remaining_usd="1",
        level="ok",
        enforcement="block",
        warn_at_pct="80",
        block_at_pct="100",
    )
    team = TeamBudgetRow(
        team="t",
        allowed_models=["m-1", "m-2"],
        budget=None,
        model_budgets={"m-1": budget},
        projects=[ProjectBudgetRow(project="p", allowed_models=["m-2"], budget=None)],
    )
    rows = re.findall(r"<tr.*?</tr>", _budget_rows([team]))
    assert len(rows) == 3
    # a row without a budget used to merge four cells, which shifted its columns when sorting
    assert [len(re.findall(r"<td", row)) for row in rows] == [10, 10, 10]
    assert "colspan" not in "".join(rows) and rows[0].count("no budget") == 1
    # a model budget's row names its model in the model column only
    assert rows[1].count("m-1") == 1 and rows[1].endswith("<td>-</td></tr>")
    # names never break in the middle: a list of models wraps between the names only
    assert rows[0].endswith(
        '<td><span class="nw">m-1</span>, <span class="nw">m-2</span></td></tr>'
    )
    assert rows[2].endswith('<td><span class="nw">m-2</span></td></tr>')
    assert rows[0].startswith('<tr data-team="t"><td class="id">t</td><td class="id">-</td>')
    from llm_finops_gateway.report import _STYLE

    assert "td.id, .nw { white-space: nowrap; }" in _STYLE
    assert '<td class="status" data-v="ok">' in rows[1]  # the note sits under the badge


def _run_entry(rid: str, team: str, use_case: str, run_id: str | None, cost: str) -> CostEntry:
    return CostEntry(
        request_id=rid,
        team=team,
        project="p",
        use_case=use_case,
        provider="fake",
        model="priced",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal(cost),
        price_book_version="test-1",
        run_id=run_id,
    )


@pytest.fixture
def runs_client(
    pricing_path: Path, price_book: PriceBook, ledger: Ledger, tmp_path: Path
) -> Iterator[TestClient]:
    ledger.record(_run_entry("a", "platform", "plan", "spec <42>", "0.3"))
    ledger.record(_run_entry("b", "platform", "implement", "spec <42>", "0.2"))
    ledger.record(_run_entry("c", "platform", "implement", None, "0.4"))
    ledger.record(_run_entry("d", "research", "plan", "other-run", "0.05"))
    with _app("api_key", pricing_path, price_book, ledger, tmp_path) as c:
        yield c


def test_summary_api_and_csv_group_by_use_case_and_run(runs_client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {RAW_FINOPS}"}
    plain = runs_client.get("/v1/finops/summary", headers=auth).json()
    assert {(r["team"], r["use_case"], r["run_id"]) for r in plain["rows"]} == {
        ("platform", None, None),
        ("research", None, None),
    }
    grouped = runs_client.get(
        "/v1/finops/summary?group_by=use_case&group_by=run_id", headers=auth
    ).json()
    assert {(r["use_case"], r["run_id"], r["cost_usd"]) for r in grouped["rows"]} == {
        ("plan", "spec <42>", "0.3"),
        ("implement", "spec <42>", "0.2"),
        ("implement", None, "0.4"),
        ("plan", "other-run", "0.05"),
    }
    one = runs_client.get(
        "/v1/finops/summary", params={"group_by": "use_case", "run_id": "spec <42>"}, headers=auth
    ).json()
    assert one["total_cost_usd"] == "0.5" and len(one["rows"]) == 2
    assert runs_client.get("/v1/finops/summary?group_by=model", headers=auth).status_code == 422

    csv_plain = runs_client.get("/v1/finops/summary.csv", headers=auth).text
    assert (
        csv_plain.splitlines()[0] == "team,project,model,calls,input_tokens,output_tokens,cost_usd"
    )
    csv_grouped = runs_client.get(
        "/v1/finops/summary.csv?group_by=use_case&group_by=run_id", headers=auth
    ).text
    assert csv_grouped.splitlines()[0] == (
        "team,project,use_case,model,run_id,calls,input_tokens,output_tokens,cost_usd"
    )
    assert "platform,p,plan,priced,spec <42>,1,10,5,0.3" in csv_grouped.splitlines()

    # a team key reads its own team's runs only
    own = runs_client.get(
        "/v1/finops/summary?group_by=run_id", headers={"Authorization": f"Bearer {RAW_PLATFORM}"}
    ).json()
    assert {r["run_id"] for r in own["rows"]} == {"spec <42>", None}


def test_report_page_shows_use_cases_runs_and_one_run(runs_client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {RAW_FINOPS}"}
    text = runs_client.get("/finops", headers=auth).text
    chargeback = text.split('id="chargeback"', 1)[1].split("</table>", 1)[0]
    assert "<th data-key>use case</th>" in chargeback
    assert "<td>plan</td>" in chargeback and "<td>implement</td>" in chargeback
    assert '<th colspan="7">total (visible rows)</th>' in chargeback
    # the runs of the month, most expensive first, each linking to its own view
    runs = text.split('<details id="runs">', 1)[1].split("</details>", 1)[0]
    assert runs.index("spec &lt;42&gt;") < runs.index("other-run") and "<42>" not in runs
    assert 'href="?run_id=spec%20%3C42%3E"' in runs and 'data-amt="0.5"' in runs

    one = runs_client.get("/finops", params={"run_id": "spec <42>"}, headers=auth).text
    assert 'run <b>spec &lt;42&gt;</b> <small><a href="?">all runs</a></small>' in one
    rows = one.split('id="chargeback"', 1)[1].split("</tbody>", 1)[0]
    assert len(re.findall(r"<tr data-team=", rows)) == 2 and "research" not in rows
    assert 'id="total" data-amt="0.5"' in one

    # a team key does not see another team's run
    mine = runs_client.get("/finops", headers={"Authorization": f"Bearer {RAW_PLATFORM}"}).text
    assert "other-run" not in mine and "spec &lt;42&gt;" in mine


def test_budget_table_has_a_row_per_use_case_budget() -> None:
    from llm_finops_gateway.models import BudgetEnvelope, ProjectBudgetRow, TeamBudgetRow
    from llm_finops_gateway.report import _budget_rows

    budget = BudgetEnvelope(
        limit_usd="2",
        spent_usd="1",
        reserved_usd="0",
        used_pct="50.0",
        remaining_usd="1",
        level="ok",
        enforcement="block",
        warn_at_pct="80",
        block_at_pct="100",
    )
    project = ProjectBudgetRow(
        project="p", allowed_models=["m-1"], budget=None, use_case_budgets={"implement": budget}
    )
    team = TeamBudgetRow(team="t", allowed_models=["m-1"], budget=None, projects=[project])
    rows = re.findall(r"<tr.*?</tr>", _budget_rows([team]))
    assert [len(re.findall(r"<td", row)) for row in rows] == [10, 10, 10]
    assert rows[2].startswith(
        '<tr data-team="t"><td class="id">t</td><td class="id">p</td>'
        '<td class="id">implement</td><td class="id">-</td>'
    )


def test_price_book_view_lists_the_long_prompt_prices(ledger: Ledger, tmp_path: Path) -> None:
    pricing = tmp_path / "pricing.yaml"
    pricing.write_text(
        "version: v\ncurrency: USD\nmodels:\n"
        "  tiered: {input: 1, output: 2, cached_input: 0.1,"
        " long_prompt: {above: 100000, input: 5, output: 10, cached_input: 0.5}}\n"
    )
    with _app("headers", pricing, PriceBook.from_yaml(pricing), ledger, tmp_path) as c:
        text = c.get("/finops").text
    assert "<td>tiered</td>" in text
    assert "<td>tiered (prompt over 100,000 tokens)</td>" in text
    row = re.search(r"<tr><td>tiered \(prompt over.*?</tr>", text)
    assert row is not None
    assert re.findall(r'data-amt="([0-9.]+)"', row.group(0)) == ["5", "10", "0.5"]
