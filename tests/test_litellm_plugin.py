"""The LiteLLM plugin, exercised through its hooks without a LiteLLM server."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastapi import HTTPException

pytest.importorskip("litellm", reason="[litellm] extra not installed")

from llm_finops_gateway.alerts import Alerter  # noqa: E402
from llm_finops_gateway.ledger import Ledger  # noqa: E402
from llm_finops_gateway.litellm_plugin import META, FinOpsPlugin  # noqa: E402
from llm_finops_gateway.policy import PolicySet  # noqa: E402
from llm_finops_gateway.pricing import PriceBook  # noqa: E402

PRICING = """\
version: "2026-11-05"
currency: USD
models:
  gpt-4o-mini: { input: 0.15, output: 0.60, cached_input: 0.075 }
"""
POLICIES = """\
version: "2026-11-05"
defaults:
  allowed_models: ["*"]
teams:
  customer-support:
    budget: { monthly_usd: 0.001, warn_at: 0.5, block_at: 1.0, reserve_output_tokens: 10 }
  research:
    budget:
      { monthly_usd: 0.001, warn_at: 0.5, block_at: 1.0, enforcement: warn,
        reserve_output_tokens: 10 }
"""


@pytest.fixture
def ledger() -> Ledger:
    return Ledger("sqlite:///:memory:")


@pytest.fixture
def plugin(tmp_path: Path, ledger: Ledger) -> FinOpsPlugin:
    (tmp_path / "pricing.yaml").write_text(PRICING)
    (tmp_path / "policies.yaml").write_text(POLICIES)
    return FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )


def detail_of(exc: HTTPException) -> dict[str, Any]:
    detail: Any = exc.detail  # starlette types it as str; the gateway raises dicts
    assert isinstance(detail, dict)
    return detail


def key(
    team: str | None = "customer-support",
    alias: str = "cs-app-1",
    metadata: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(team_alias=team, team_id="t-1", key_alias=alias, metadata=metadata or {})


def request(
    model: str = "gpt-4o-mini", project: str | None = "chat-assistant", **extra: Any
) -> dict[str, Any]:
    headers = {"content-type": "application/json"}
    if project:
        headers["x-project"] = project
    data: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 5,
        "metadata": {"endpoint": "/chat/completions"},
        "proxy_server_request": {"headers": headers},
    }
    data.update(extra)
    return data


def response(prompt: int = 9, completion: int = 1, cached: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
            completion_tokens_details=None,
        ),
        _hidden_params={"custom_llm_provider": "azure"},
    )


def success_kwargs(data: dict[str, Any], **slo: Any) -> dict[str, Any]:
    return {
        "litellm_params": {"metadata": data["metadata"]},
        "standard_logging_object": {"custom_llm_provider": "azure", **slo},
        "response_cost": 5.2e-06,
    }


async def test_missing_project_is_400_before_the_call(plugin: FinOpsPlugin) -> None:
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(key(), None, request(project=None), "acompletion")
    assert exc.value.status_code == 400
    detail = detail_of(exc.value)
    # LiteLLM hands only `error` to the client as the message: the explanation rides along,
    # the bare code is in `code` (ADR-0034)
    assert detail["code"] == "missing_attribution"
    assert detail["error"].startswith("missing_attribution: Set X-Project")
    assert detail["missing_headers"] == ["X-Project"]
    assert "X-Team" not in detail["hint"]


async def test_key_metadata_supplies_the_default_project_and_use_case(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    bot = key(alias="cs-bot", metadata={META: {"project": "chat-assistant", "use_case": "faq"}})
    data = await plugin.async_pre_call_hook(bot, None, request(project=None), "acompletion")
    assert data is not None
    await plugin.async_post_call_response_headers_hook(data, bot, response())
    row = ledger.rows(datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))[0]
    assert (row.project, row.use_case, row.key_id) == ("chat-assistant", "faq", "cs-bot")
    # an explicit header wins over the key's default
    data = await plugin.async_pre_call_hook(
        bot, None, request(project="internal-tools"), "acompletion"
    )
    assert data is not None
    await plugin.async_post_call_response_headers_hook(data, bot, response())
    rows = ledger.rows(datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    assert {r.project for r in rows} == {"chat-assistant", "internal-tools"}


PINNED = {META: {"project": "chat-assistant", "use_case": "plan", "pin": True}}
ALL_TIME = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


async def test_pinned_key_rejects_a_header_that_contradicts_it(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    pinned = key(alias="plan-key", metadata=PINNED)
    # another phase than the key's
    data = request()
    data["proxy_server_request"]["headers"]["x-use-case"] = "implement"
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(pinned, None, data, "acompletion")
    assert exc.value.status_code == 403
    detail = detail_of(exc.value)
    assert detail["code"] == "use_case_not_allowed"
    assert detail["error"] == (
        "use_case_not_allowed: key 'plan-key' may only be used for use case 'plan', not 'implement'"
    )
    # another project than the key's, on the route a coding agent uses
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(
            pinned, None, responses_request(project="internal-tools"), "aresponses"
        )
    assert exc.value.status_code == 403
    detail = detail_of(exc.value)
    assert detail["code"] == "project_not_allowed"
    assert detail["error"] == (
        "project_not_allowed: key 'plan-key' may only be used for project 'chat-assistant', "
        "not 'internal-tools'"
    )
    # rejected before the policy check: nothing reserved, nothing recorded
    assert ledger.counter("customer-support").reserved_usd == 0
    assert ledger.rows(*ALL_TIME) == []


async def test_pinned_key_serves_a_call_that_agrees_or_says_nothing(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    pinned = key(alias="plan-key", metadata=PINNED)
    agreeing = request()
    agreeing["proxy_server_request"]["headers"]["x-use-case"] = "plan"
    for data in (request(project=None), agreeing):
        served = await plugin.async_pre_call_hook(pinned, None, data, "acompletion")
        assert served is not None
        await plugin.async_post_call_response_headers_hook(served, pinned, response())
    rows = ledger.rows(*ALL_TIME)
    assert [(r.project, r.use_case) for r in rows] == [("chat-assistant", "plan")] * 2


async def test_pin_binds_only_what_the_key_carries(plugin: FinOpsPlugin) -> None:
    # the key names a use case and no project: the project stays the caller's to say
    phase = key(alias="phase-key", metadata={META: {"use_case": "plan", "pin": True}})
    data = await plugin.async_pre_call_hook(
        phase, None, request(project="internal-tools"), "acompletion"
    )
    assert data is not None
    # and only a literal true pins: anything else leaves the values as defaults
    loose = key(alias="loose-key", metadata={META: {"project": "chat-assistant", "pin": "yes"}})
    data = await plugin.async_pre_call_hook(
        loose, None, request(project="internal-tools"), "acompletion"
    )
    assert data is not None


async def test_key_without_team_is_400(plugin: FinOpsPlugin) -> None:
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(key(team=None), None, request(), "acompletion")
    assert exc.value.status_code == 400
    assert detail_of(exc.value)["missing"] == ["team"]


async def test_unknown_model_is_422_before_the_call(plugin: FinOpsPlugin) -> None:
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(key(), None, request(model="mystery"), "acompletion")
    assert exc.value.status_code == 422
    assert "mystery" in str(exc.value.detail)


async def test_non_chat_calls_pass_through(plugin: FinOpsPlugin) -> None:
    data = request(model="mystery", project=None)
    assert await plugin.async_pre_call_hook(key(), None, data, "aembedding") is data


async def test_allowed_call_reserves_and_settles_in_the_headers_hook(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    data = await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    assert data is not None
    rid = data["metadata"][META]["request_id"]
    assert rid.startswith("lt-")
    assert ledger.counter("customer-support").reserved_usd > 0

    headers = await plugin.async_post_call_response_headers_hook(data, key(), response())
    assert headers is not None
    assert headers["X-FinOps-Request-Id"] == rid
    assert headers["X-FinOps-Currency"] == "USD"
    # 9 input at 0.15 / 1M + 1 output at 0.60 / 1M
    assert headers["X-FinOps-Cost-USD"] == "0.00000195"
    assert headers["X-FinOps-Budget-Status"] == "ok"
    counter = ledger.counter("customer-support")
    assert counter.reserved_usd == 0
    assert counter.spent_usd == Decimal("0.00000195")

    # The success event sees the request as settled and does not record it twice.
    await plugin.async_log_success_event(success_kwargs(data), response(), None, None)
    assert ledger.counter("customer-support").spent_usd == Decimal("0.00000195")
    assert plugin._pending == {}


async def test_streaming_settles_from_the_success_event(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    data = await plugin.async_pre_call_hook(key(), None, request(stream=True), "acompletion")
    assert data is not None
    stream_wrapper = SimpleNamespace()  # no usage yet
    headers = await plugin.async_post_call_response_headers_hook(data, key(), stream_wrapper)
    assert headers is not None
    assert "X-FinOps-Cost-USD" not in headers
    assert headers["X-FinOps-Budget-Status"] == "ok"
    assert ledger.counter("customer-support").reserved_usd > 0

    await plugin.async_log_success_event(
        success_kwargs(data, prompt_tokens=9, completion_tokens=1), response(), None, None
    )
    counter = ledger.counter("customer-support")
    assert counter.reserved_usd == 0
    assert counter.spent_usd == Decimal("0.00000195")


async def test_failure_releases_the_reservation(plugin: FinOpsPlugin, ledger: Ledger) -> None:
    data = await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    assert data is not None
    assert ledger.counter("customer-support").reserved_usd > 0
    await plugin.async_log_failure_event(success_kwargs(data), None, None, None)
    counter = ledger.counter("customer-support")
    assert counter.reserved_usd == 0 and counter.spent_usd == 0


async def test_cached_tokens_are_priced_at_the_cached_rate(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    data = await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    assert data is not None
    headers = await plugin.async_post_call_response_headers_hook(
        data, key(), response(prompt=100, completion=0, cached=100)
    )
    assert headers is not None
    assert headers["X-FinOps-Cost-USD"] == "0.0000075"  # 100 cached at 0.075 / 1M
    assert headers["X-FinOps-Cached-Input-Tokens"] == "100"


async def test_budget_blocks_with_403_once_spent(plugin: FinOpsPlugin, ledger: Ledger) -> None:
    data = await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    assert data is not None
    await plugin.async_post_call_response_headers_hook(
        data, key(), response(prompt=4000, completion=4000)
    )  # 4000 * 0.15 + 4000 * 0.60 per million = 0.003 USD, over the 0.001 budget
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    assert exc.value.status_code == 403
    detail = detail_of(exc.value)
    assert detail["code"] == "budget_exceeded"
    assert detail["error"].startswith("budget_exceeded: team 'customer-support' has used")
    assert detail["budget"]["level"] == "block"


async def test_warn_only_team_keeps_going_with_a_warning_header(plugin: FinOpsPlugin) -> None:
    k = key(team="research", alias="lab")
    data = await plugin.async_pre_call_hook(k, None, request(), "acompletion")
    assert data is not None
    await plugin.async_post_call_response_headers_hook(
        data, k, response(prompt=4000, completion=4000)
    )
    data2 = await plugin.async_pre_call_hook(k, None, request(), "acompletion")
    assert data2 is not None
    headers = await plugin.async_post_call_response_headers_hook(data2, k, response())
    assert headers is not None
    # enforcement: warn never blocks; the level stays "warn" past 100% and the header says so
    assert headers["X-FinOps-Budget-Status"] == "warn"
    assert "X-FinOps-Warning" in headers


async def test_hook_signatures_match_the_pinned_litellm(plugin: FinOpsPlugin) -> None:
    """Contract test: a LiteLLM upgrade that changes a hook's parameters fails here."""
    import inspect

    from litellm.integrations.custom_logger import CustomLogger

    for name in (
        "async_pre_call_hook",
        "async_post_call_response_headers_hook",
        "async_log_success_event",
        "async_log_failure_event",
    ):
        base = list(inspect.signature(getattr(CustomLogger, name)).parameters)
        ours = list(inspect.signature(getattr(FinOpsPlugin, name)).parameters)
        assert ours == base, f"{name}: plugin {ours} != litellm {base}"


REGISTRY_POLICIES = """\
version: "registry"
defaults:
  allowed_models: ["*"]
teams:
  product:
    budget: { monthly_usd: 1, warn_at: 0.8, block_at: 1.0, reserve_output_tokens: 10 }
    projects:
      chat-assistant:
        budget: { monthly_usd: 0.000005, warn_at: 0.5, block_at: 1.0, reserve_output_tokens: 10 }
"""


async def test_project_registry_and_project_budget_in_litellm_mode(
    tmp_path: Path, ledger: Ledger
) -> None:
    (tmp_path / "pricing.yaml").write_text(PRICING)
    (tmp_path / "policies.yaml").write_text(REGISTRY_POLICIES)
    p = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )
    k = key(team="product", alias="app")
    with pytest.raises(HTTPException) as exc:
        await p.async_pre_call_hook(k, None, request(project="pilot-7"), "acompletion")
    assert exc.value.status_code == 403
    assert detail_of(exc.value)["code"] == "project_not_registered"

    data = await p.async_pre_call_hook(k, None, request(), "acompletion")
    assert data is not None
    assert ledger.counter("product/chat-assistant").reserved_usd > 0
    headers = await p.async_post_call_response_headers_hook(data, k, response())
    assert headers is not None
    assert headers["X-FinOps-Project-Budget-Limit-USD"] == "0.000005"
    assert headers["X-FinOps-Project-Budget-Status"] == "ok"
    assert headers["X-FinOps-Budget-Status"] == "ok"
    assert ledger.counter("product/chat-assistant").reserved_usd == 0
    assert ledger.counter("product/chat-assistant").spent_usd == Decimal("0.00000195")


async def test_plugin_emits_the_gateway_metrics(tmp_path: Path, ledger: Ledger) -> None:
    from llm_finops_gateway.metrics import Metrics

    (tmp_path / "pricing.yaml").write_text(PRICING)
    (tmp_path / "policies.yaml").write_text(POLICIES)
    metrics = Metrics()
    p = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
        metrics=metrics,
    )
    with pytest.raises(HTTPException):
        await p.async_pre_call_hook(key(), None, request(model="mystery"), "acompletion")
    data = await p.async_pre_call_hook(key(), None, request(), "acompletion")
    assert data is not None
    await p.async_post_call_response_headers_hook(data, key(), response())
    data2 = await p.async_pre_call_hook(key(), None, request(stream=True), "acompletion")
    assert data2 is not None
    from datetime import datetime, timedelta

    t0 = datetime(2026, 11, 5, 12, 0, 0)
    await p.async_log_success_event(
        success_kwargs(data2), response(), t0, t0 + timedelta(milliseconds=750)
    )
    text = metrics.render().decode()
    cs = 'team="customer-support"'
    assert f'lfg_requests_total{{model="mystery",outcome="unpriced",{cs}}} 1.0' in text
    assert f'lfg_requests_total{{model="gpt-4o-mini",outcome="ok",{cs}}} 2.0' in text
    assert f'lfg_cost_usd_total{{model="gpt-4o-mini",project="chat-assistant",{cs}}}' in text
    assert f'lfg_tokens_total{{direction="input",model="gpt-4o-mini",{cs}}} 18.0' in text
    assert 'lfg_upstream_latency_seconds_count{model="gpt-4o-mini",provider="azure"} 1.0' in text
    assert f"lfg_budget_used_ratio{{{cs}}}" in text


@respx.mock
async def test_plugin_posts_the_same_webhook_alerts_as_the_gateway(
    tmp_path: Path, ledger: Ledger
) -> None:
    hook = "https://hooks.example.test/finops"
    route = respx.post(hook).mock(return_value=httpx.Response(200, text="ok"))
    (tmp_path / "pricing.yaml").write_text(PRICING)
    (tmp_path / "policies.yaml").write_text(POLICIES)
    alerter = Alerter(hook, cooldown_seconds=0)
    plugin = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
        alerter=alerter,
    )
    # research is warn-only: the first call past warn_at raises a budget_warn
    data = await plugin.async_pre_call_hook(key(team="research"), None, request(), "acompletion")
    assert data is not None
    await plugin.async_post_call_response_headers_hook(
        data, key(team="research"), response(prompt=4000, completion=0)
    )
    data = await plugin.async_pre_call_hook(key(team="research"), None, request(), "acompletion")
    assert data is not None
    # customer-support blocks: a budget_block
    data2 = await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    assert data2 is not None
    await plugin.async_post_call_response_headers_hook(
        data2, key(), response(prompt=10000, completion=0)
    )
    with pytest.raises(HTTPException):
        await plugin.async_pre_call_hook(key(), None, request(), "acompletion")
    # an upstream failure
    data3 = await plugin.async_pre_call_hook(key(team="research"), None, request(), "acompletion")
    assert data3 is not None
    await plugin.async_log_failure_event(
        {**success_kwargs(data3), "exception": RuntimeError("boom")}, None, 0.0, 1.0
    )
    await alerter.aclose()
    events = [json.loads(c.request.content)["event"] for c in route.calls]
    # research warns again on the third call (cooldown 0), then the failure alert
    assert events == ["budget_warn", "budget_block", "budget_warn", "upstream_error"]
    body = json.loads(route.calls[3].request.content)
    assert (
        body["provider"] == "azure" and body["model"] == "gpt-4o-mini" and "boom" in body["message"]
    )


MODEL_BUDGET_POLICIES = """\
version: "model-budgets"
defaults:
  allowed_models: ["*"]
teams:
  product:
    budget: { monthly_usd: 1, warn_at: 0.8, block_at: 1.0, reserve_output_tokens: 10 }
    model_budgets:
      gpt-4o-mini: { monthly_usd: 0.000005, warn_at: 0.5, block_at: 1.0 }
"""


async def test_model_budget_in_litellm_mode(tmp_path: Path, ledger: Ledger) -> None:
    (tmp_path / "pricing.yaml").write_text(PRICING)
    (tmp_path / "policies.yaml").write_text(MODEL_BUDGET_POLICIES)
    p = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )
    k = key(team="product", alias="app")
    data = await p.async_pre_call_hook(k, None, request(), "acompletion")
    assert data is not None
    assert ledger.counter("product/model/gpt-4o-mini").reserved_usd > 0
    headers = await p.async_post_call_response_headers_hook(data, k, response())
    assert headers is not None
    assert headers["X-FinOps-Model-Budget-Limit-USD"] == "0.000005"
    assert headers["X-FinOps-Model-Budget-Status"] == "ok"
    c = ledger.counter("product/model/gpt-4o-mini")
    assert c.reserved_usd == 0 and c.spent_usd == Decimal("0.00000195")

    ledger.reserve("product/model/gpt-4o-mini", Decimal("0.000005"), None)
    with pytest.raises(HTTPException) as exc:
        await p.async_pre_call_hook(k, None, request(), "acompletion")
    assert exc.value.status_code == 403
    assert detail_of(exc.value)["model_budget"]["level"] == "block"
    assert ledger.counter("product").reserved_usd == 0


# ---- Claude Code and other Anthropic Messages clients on /v1/messages (ADR-0027) -----------

CLAUDE_PRICING = """\
version: "claude"
currency: USD
models:
  claude-sonnet-5-5:
    { input: 2, output: 10, cached_input: 0.1, cache_write: 2.5, cache_write_1h: 4 }
"""
CLAUDE_POLICIES = """\
version: "claude"
defaults:
  allowed_models: ["*"]
teams:
  dev:
    budget: { monthly_usd: 5 }
    model_budgets:
      claude-sonnet-5-5: { monthly_usd: 1 }
"""


@pytest.fixture
def claude_plugin(tmp_path: Path, ledger: Ledger) -> FinOpsPlugin:
    (tmp_path / "pricing.yaml").write_text(CLAUDE_PRICING)
    (tmp_path / "policies.yaml").write_text(CLAUDE_POLICIES)
    return FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )


def messages_request(project: str | None = "my-repo", **extra: Any) -> dict[str, Any]:
    """The pre-call data of a `/v1/messages` request as LiteLLM 1.108 hands it over: the proxy's
    own metadata is under `litellm_metadata`, `metadata` is the Anthropic request field."""
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if project:
        headers["x-project"] = project
    data: dict[str, Any] = {
        "model": "claude-sonnet-5-5",
        "max_tokens": 100,
        "system": [{"type": "text", "text": "You are Claude Code. " + "s" * 4000}],
        "tools": [{"name": "Bash", "description": "run", "input_schema": {"type": "object"}}],
        "metadata": {"user_id": "user_abc"},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"c": "ls"}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a.txt"}],
            },
        ],
        "litellm_metadata": {"endpoint": "/v1/messages"},
        "proxy_server_request": {"headers": headers},
    }
    data.update(extra)
    return data


def messages_success(data: dict[str, Any]) -> tuple[dict[str, Any], SimpleNamespace]:
    """The success event of that request: LiteLLM's usage, cache reads and writes included."""
    kwargs = {
        "litellm_params": {
            "metadata": data["litellm_metadata"],
            "litellm_metadata": data["litellm_metadata"],
        },
        "standard_logging_object": {"custom_llm_provider": "anthropic"},
        "response_cost": 0.01174,
    }
    usage = SimpleNamespace(
        prompt_tokens=23_120,  # 120 fresh + 20,000 read from the cache + 3,000 written to it
        completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=20_000,
            cache_creation_tokens=3_000,
            cache_creation_token_details={
                "ephemeral_5m_input_tokens": 2_000,
                "ephemeral_1h_input_tokens": 1_000,
            },
        ),
        completion_tokens_details=None,
    )
    return kwargs, SimpleNamespace(usage=usage)


async def test_messages_call_is_attributed_reserved_and_settled_with_cache_tokens(
    claude_plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    k = key(team="dev", alias="dev-laptop")
    data = await claude_plugin.async_pre_call_hook(
        k, None, messages_request(), "anthropic_messages"
    )
    assert data is not None
    assert data["metadata"] == {"user_id": "user_abc"}  # Anthropic's field is left alone
    assert data["litellm_metadata"]["finops"]["team"] == "dev"
    # the estimate counts the system prompt, the tools and the content blocks: 100 output
    # tokens alone are 0.001, the 4,000-character system prompt adds about 1,000 input tokens
    reserved = ledger.counter("dev").reserved_usd
    assert reserved > Decimal("0.003")
    assert ledger.counter("dev/model/claude-sonnet-5-5").reserved_usd == reserved

    # a non-streaming /v1/messages response is a plain dict in the Anthropic shape; the call is
    # settled from the success event, which carries the provider and the full usage
    anthropic_body = {"type": "message", "usage": {"input_tokens": 120, "output_tokens": 50}}
    headers = await claude_plugin.async_post_call_response_headers_hook(data, k, anthropic_body)
    assert headers is not None and "X-FinOps-Cost-USD" not in headers
    assert headers["X-FinOps-Budget-Status"] == "ok"
    assert headers["X-FinOps-Model-Budget-Limit-USD"] == "1"
    assert ledger.counter("dev").reserved_usd == reserved

    kwargs, response_obj = messages_success(data)
    await claude_plugin.async_log_success_event(kwargs, response_obj, None, None)
    counter = ledger.counter("dev")
    assert counter.reserved_usd == 0 and counter.spent_usd == Decimal("0.01174")
    assert ledger.counter("dev/model/claude-sonnet-5-5").spent_usd == Decimal("0.01174")
    now = datetime.now(UTC)
    (row,) = ledger.rows(now - timedelta(hours=1), now + timedelta(hours=1))
    assert (row.team, row.project, row.key_id) == ("dev", "my-repo", "dev-laptop")
    assert (row.provider, row.model) == ("anthropic", "claude-sonnet-5-5")
    assert (row.input_tokens, row.output_tokens, row.cached_input_tokens) == (23_120, 50, 20_000)
    assert (row.cache_write_input_tokens, row.cache_write_1h_input_tokens) == (3_000, 1_000)


async def test_messages_call_follows_the_same_rejections(claude_plugin: FinOpsPlugin) -> None:
    k = key(team="dev", alias="dev-laptop")
    with pytest.raises(HTTPException) as exc:
        await claude_plugin.async_pre_call_hook(
            k, None, messages_request(project=None), "anthropic_messages"
        )
    assert exc.value.status_code == 400
    # LiteLLM hands only `error` to the client as the message, and Claude Code shows only
    # that: on this route the explanation rides along, the bare code is in `code`
    detail = detail_of(exc.value)
    assert detail["code"] == "missing_attribution"
    assert detail["error"].startswith("missing_attribution: Set X-Project")

    with pytest.raises(HTTPException) as exc:
        await claude_plugin.async_pre_call_hook(
            k, None, messages_request(model="claude-unknown"), "anthropic_messages"
        )
    assert exc.value.status_code == 422


async def test_messages_budget_block_tells_the_developer_which_budget(
    claude_plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    ledger.reserve("dev/model/claude-sonnet-5-5", Decimal("1"), None)  # the model budget is full
    with pytest.raises(HTTPException) as exc:
        await claude_plugin.async_pre_call_hook(
            key(team="dev", alias="dev-laptop"), None, messages_request(), "anthropic_messages"
        )
    assert exc.value.status_code == 403
    detail = detail_of(exc.value)
    assert detail["code"] == "budget_exceeded" and detail["model_budget"]["level"] == "block"
    assert detail["error"].startswith(
        "budget_exceeded: model 'claude-sonnet-5-5' of team 'dev' has used 100.0% of its monthly"
    )
    assert ledger.counter("dev").reserved_usd == 0

    # the chat route speaks the same way (ADR-0034)
    with pytest.raises(HTTPException) as exc:
        await claude_plugin.async_pre_call_hook(
            key(team="dev", alias="dev-laptop"),
            None,
            request(model="claude-sonnet-5-5", project="my-repo"),
            "acompletion",
        )
    assert detail_of(exc.value)["code"] == "budget_exceeded"
    assert detail_of(exc.value)["error"].startswith("budget_exceeded: model 'claude-sonnet-5-5'")


async def test_messages_failure_releases_the_reservation(
    claude_plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    k = key(team="dev", alias="dev-laptop")
    data = await claude_plugin.async_pre_call_hook(
        k, None, messages_request(), "anthropic_messages"
    )
    assert data is not None and ledger.counter("dev").reserved_usd > 0
    kwargs = {"litellm_params": {"litellm_metadata": data["litellm_metadata"]}}
    await claude_plugin.async_log_failure_event(kwargs, None, None, None)
    assert ledger.counter("dev").reserved_usd == 0
    assert ledger.counter("dev/model/claude-sonnet-5-5").reserved_usd == 0


async def test_messages_reservation_respects_the_output_cap(tmp_path: Path, ledger: Ledger) -> None:
    (tmp_path / "pricing.yaml").write_text(CLAUDE_PRICING)
    (tmp_path / "policies.yaml").write_text(
        CLAUDE_POLICIES.replace(
            "budget: { monthly_usd: 5 }",
            "budget: { monthly_usd: 5, reserve_output_tokens_max: 8000 }",
        )
    )
    p = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )
    k = key(team="dev", alias="dev-laptop")
    # Claude Code asks for 128,000 output tokens: 1.28 USD on this row, more than the 1 USD
    # model budget. With the cap the reservation assumes 8,000: 0.08 USD plus the prompt.
    data = await p.async_pre_call_hook(
        k, None, messages_request(max_tokens=128_000), "anthropic_messages"
    )
    assert data is not None and data["max_tokens"] == 128_000
    reserved = ledger.counter("dev").reserved_usd
    assert Decimal("0.08") < reserved < Decimal("0.09")
    assert ledger.counter("dev/model/claude-sonnet-5-5").reserved_usd == reserved


def responses_request(project: str | None = "chat-assistant", **extra: Any) -> dict[str, Any]:
    """The pre-call data of a `/v1/responses` request as LiteLLM 1.108 hands it over (measured
    with Copilot CLI 1.0.114): `input` and `instructions` instead of `messages`, no
    `max_tokens`, the proxy's own metadata under `litellm_metadata`."""
    headers = {"content-type": "application/json", "user-agent": "OpenAI/JS 5.20.1"}
    if project:
        headers["x-project"] = project
    data: dict[str, Any] = {
        "model": "gpt-4o-mini",
        "instructions": "You are a coding agent. " + "s" * 4000,
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"c": "ls"}'},
            {"type": "function_call_output", "call_id": "c1", "output": "a.txt"},
        ],
        "tools": [{"type": "function", "name": "bash", "parameters": {"type": "object"}}],
        "stream": True,
        "litellm_metadata": {"endpoint": "/v1/responses"},
        "proxy_server_request": {"headers": headers},
    }
    data.update(extra)
    return data


def responses_success(data: dict[str, Any]) -> tuple[dict[str, Any], SimpleNamespace]:
    """The success event of that request: LiteLLM hands the usage over in the chat shape."""
    kwargs = {
        "litellm_params": {"litellm_metadata": data["litellm_metadata"]},
        "standard_logging_object": {"custom_llm_provider": "openai"},
        "response_cost": 8.9e-05,
    }
    return kwargs, response(prompt=1200, completion=7, cached=1000)


async def test_responses_call_is_attributed_reserved_and_settled(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    k = key(metadata={"finops": {"use_case": "implement"}})
    data = await plugin.async_pre_call_hook(k, None, responses_request(), "aresponses")
    assert data is not None
    assert "metadata" not in data  # on this route `metadata` is the client's own field
    assert data["litellm_metadata"]["finops"]["team"] == "customer-support"
    # the 4,000-character instructions alone are about 1,000 input tokens: 0.00015
    reserved = ledger.counter("customer-support").reserved_usd
    assert Decimal("0.00015") < reserved < Decimal("0.0002")

    # the response object carries input_tokens / output_tokens; like /v1/messages, the call is
    # settled from the success event
    body = SimpleNamespace(usage=SimpleNamespace(input_tokens=1200, output_tokens=7))
    headers = await plugin.async_post_call_response_headers_hook(data, k, body)
    assert headers is not None and "X-FinOps-Cost-USD" not in headers
    assert ledger.counter("customer-support").reserved_usd == reserved

    kwargs, response_obj = responses_success(data)
    await plugin.async_log_success_event(kwargs, response_obj, None, None)
    counter = ledger.counter("customer-support")
    # 200 fresh input + 1,000 cached input + 7 output tokens
    assert counter.reserved_usd == 0 and counter.spent_usd == Decimal("0.0001092")
    now = datetime.now(UTC)
    (row,) = ledger.rows(now - timedelta(hours=1), now + timedelta(hours=1))
    assert (row.team, row.project) == ("customer-support", "chat-assistant")
    assert row.use_case == "implement"  # from the key metadata, no header needed
    assert (row.provider, row.model) == ("openai", "gpt-4o-mini")
    assert (row.input_tokens, row.output_tokens, row.cached_input_tokens) == (1200, 7, 1000)


async def test_responses_call_follows_the_same_rejections(plugin: FinOpsPlugin) -> None:
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(key(), None, responses_request(project=None), "aresponses")
    assert exc.value.status_code == 400
    # a coding agent shows its user only the message: the explanation rides along with the code
    detail = detail_of(exc.value)
    assert detail["code"] == "missing_attribution"
    assert detail["error"].startswith("missing_attribution: Set X-Project")

    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(
            key(), None, responses_request(model="gpt-unknown"), "aresponses"
        )
    assert exc.value.status_code == 422


async def test_responses_estimate_reads_max_output_tokens_and_a_plain_input(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    small = responses_request(input="Say OK", instructions=None, tools=None)
    assert await plugin.async_pre_call_hook(key(), None, small, "responses") is not None
    assert ledger.counter("customer-support").reserved_usd < Decimal("0.00001")

    # 100,000 output tokens are 0.06, far above the 0.001 budget
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(
            key(), None, responses_request(max_output_tokens=100_000), "aresponses"
        )
    assert exc.value.status_code == 403
    assert detail_of(exc.value)["code"] == "budget_exceeded"


async def test_responses_failure_releases_the_reservation(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    data = await plugin.async_pre_call_hook(key(), None, responses_request(), "aresponses")
    assert data is not None and ledger.counter("customer-support").reserved_usd > 0
    kwargs, _ = responses_success(data)
    await plugin.async_log_failure_event({**kwargs, "exception": "boom"}, None, None, None)
    assert ledger.counter("customer-support").reserved_usd == 0
    assert ledger.counter("customer-support").spent_usd == 0


async def test_run_id_header_reaches_the_ledger_and_the_response(
    plugin: FinOpsPlugin, ledger: Ledger
) -> None:
    data = request()
    data["proxy_server_request"]["headers"]["x-run-id"] = "spec-42"
    data = await plugin.async_pre_call_hook(key(), None, data, "acompletion") or {}
    headers = await plugin.async_post_call_response_headers_hook(data, key(), response())
    assert headers is not None and headers["X-FinOps-Run-Id"] == "spec-42"
    now = datetime.now(UTC)
    (row,) = ledger.rows(now - timedelta(hours=1), now + timedelta(hours=1))
    assert row.run_id == "spec-42"

    long = request()
    long["proxy_server_request"]["headers"]["x-run-id"] = "r" * 129
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(key(), None, long, "acompletion")
    assert exc.value.status_code == 400


async def test_use_case_rules_in_litellm_mode(tmp_path: Path, ledger: Ledger) -> None:
    (tmp_path / "pricing.yaml").write_text(PRICING)
    (tmp_path / "policies.yaml").write_text(
        'version: "uc"\ndefaults:\n  allowed_models: ["*"]\nteams:\n  dev:\n'
        "    projects:\n      checkout-web:\n        use_cases:\n"
        '          plan: {allowed_models: ["other-model"]}\n'
        "          implement: {budget: {monthly_usd: 1, reserve_output_tokens: 10}}\n"
    )
    plugin = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )
    k = key(team="dev", alias="dev-key", metadata={"finops": {"use_case": "implement"}})
    data = await plugin.async_pre_call_hook(k, None, request(project="checkout-web"), "acompletion")
    assert data is not None
    scope = "dev/checkout-web/use_case/implement"
    assert ledger.counter(scope).reserved_usd > 0
    headers = await plugin.async_post_call_response_headers_hook(data, k, response())
    assert headers is not None and headers["X-FinOps-Use-Case-Budget-Limit-USD"] == "1"
    counter = ledger.counter(scope)
    assert counter.reserved_usd == 0 and counter.spent_usd == Decimal("0.00000195")

    planning = key(team="dev", alias="dev-key", metadata={"finops": {"use_case": "plan"}})
    with pytest.raises(HTTPException) as exc:
        await plugin.async_pre_call_hook(
            planning, None, request(project="checkout-web"), "acompletion"
        )
    assert exc.value.status_code == 403
    assert detail_of(exc.value)["code"] == "model_not_allowed"
    assert "use case 'plan' of project 'checkout-web'" in detail_of(exc.value)["error"]

    # a failed call gives the use case reservation back too
    data = await plugin.async_pre_call_hook(k, None, request(project="checkout-web"), "acompletion")
    assert data is not None and ledger.counter(scope).reserved_usd > 0
    await plugin.async_log_failure_event(success_kwargs(data), None, None, None)
    assert ledger.counter(scope).reserved_usd == 0


async def test_alias_in_litellm_mode_is_priced_and_listed_as_its_target(
    tmp_path: Path, ledger: Ledger
) -> None:
    (tmp_path / "pricing.yaml").write_text(PRICING + "  sdd-plan: { alias_of: gpt-4o-mini }\n")
    (tmp_path / "policies.yaml").write_text(
        'version: "a"\ndefaults:\n  allowed_models: ["gpt-4o-mini"]\n'
        "teams:\n  dev:\n    model_budgets:\n      gpt-4o-mini: {monthly_usd: 1}\n"
    )
    plugin = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )
    k = key(team="dev", alias="dev-key")
    data = await plugin.async_pre_call_hook(k, None, request(model="sdd-plan"), "acompletion")
    assert data is not None and data["model"] == "sdd-plan"  # LiteLLM routes the alias itself
    assert ledger.counter("dev/model/gpt-4o-mini").reserved_usd > 0
    await plugin.async_post_call_response_headers_hook(data, k, response())
    now = datetime.now(UTC)
    (row,) = ledger.rows(now - timedelta(hours=1), now + timedelta(hours=1))
    assert (row.model, row.model_alias, row.cost_usd) == (
        "gpt-4o-mini",
        "sdd-plan",
        Decimal("0.00000195"),
    )


async def test_alias_with_a_use_case_carries_the_phase_in_litellm_mode(
    tmp_path: Path, ledger: Ledger
) -> None:
    (tmp_path / "pricing.yaml").write_text(
        PRICING + "  sdd-plan: { alias_of: gpt-4o-mini, use_case: plan }\n"
    )
    (tmp_path / "policies.yaml").write_text(
        'version: "a"\ndefaults:\n  allowed_models: ["gpt-4o-mini"]\n'
    )
    plugin = FinOpsPlugin(
        price_book=PriceBook.from_yaml(tmp_path / "pricing.yaml"),
        policies=PolicySet.from_yaml(tmp_path / "policies.yaml"),
        ledger=ledger,
    )
    # one key, no header: the model name says the phase; a key's default does not override it
    plain = key(team="dev", alias="dev-key")
    loose = key(team="dev", alias="loose-key", metadata={META: {"use_case": "implement"}})
    for k in (plain, loose):
        data = await plugin.async_pre_call_hook(k, None, request(model="sdd-plan"), "acompletion")
        assert data is not None
        await plugin.async_post_call_response_headers_hook(data, k, response())
    assert [(r.use_case, r.model_alias) for r in ledger.rows(*ALL_TIME)] == [
        ("plan", "sdd-plan")
    ] * 2

    # a header that says another phase, and a key pinned to another phase, are refused
    claiming = request(model="sdd-plan")
    claiming["proxy_server_request"]["headers"]["x-use-case"] = "implement"
    pinned = key(
        team="dev", alias="impl-key", metadata={META: {"use_case": "implement", "pin": True}}
    )
    for k, data in ((plain, claiming), (pinned, request(model="sdd-plan"))):
        with pytest.raises(HTTPException) as exc:
            await plugin.async_pre_call_hook(k, None, data, "acompletion")
        assert exc.value.status_code == 403
        detail = detail_of(exc.value)
        assert detail["code"] == "use_case_not_allowed"
        assert detail["error"] == (
            "use_case_not_allowed: model 'sdd-plan' is for use case 'plan', not 'implement'"
        )
    assert len(ledger.rows(*ALL_TIME)) == 2
