from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from llm_finops_gateway.pricing import PriceBook, PriceBookError, UnknownModelError, money


def test_cost_is_exact_decimal(price_book: PriceBook) -> None:
    assert price_book.cost("priced", 1_000_000, 100_000) == Decimal("3.5")


def test_cost_small_call_keeps_precision(price_book: PriceBook) -> None:
    assert price_book.cost("priced", 123, 45) == Decimal("0.0007575")


def test_free_model_costs_zero(price_book: PriceBook) -> None:
    assert price_book.cost("free", 10_000, 10_000) == Decimal("0")


def test_unknown_model_fails_loudly(price_book: PriceBook) -> None:
    with pytest.raises(UnknownModelError) as exc:
        price_book.cost("nope", 1, 1)
    assert "nope" in str(exc.value)


def test_negative_tokens_rejected(price_book: PriceBook) -> None:
    with pytest.raises(ValueError):
        price_book.cost("priced", -1, 0)


def test_version_and_models(price_book: PriceBook) -> None:
    assert price_book.version == "test-1"
    assert price_book.currency == "USD"
    assert price_book.models() == ["free", "priced"]
    assert price_book.has("priced") and not price_book.has("nope")


@pytest.mark.parametrize(
    "content",
    [
        "version: 1\ncurrency: USD\n",  # missing models
        "version: 1\ncurrency: USD\nmodels: [a, b]\n",  # models not a mapping
        "version: 1\ncurrency: USD\nmodels:\n  m: {input: 1}\n",  # missing output
        "version: 1\ncurrency: dollars\nmodels:\n  m: {input: 1, output: 1}\n",  # bad currency
        "- not\n- a\n- mapping\n",
    ],
)
def test_malformed_price_book_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    with pytest.raises(PriceBookError):
        PriceBook.from_yaml(path)


def test_repo_price_book_loads() -> None:
    book = PriceBook.from_yaml(Path(__file__).parent.parent / "config" / "pricing.yaml")
    assert book.has("gpt-4o-mini")
    assert book.cost("gpt-4o-mini", 1_000_000, 0) == Decimal("0.15")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0"),
        (Decimal("0E-8"), "0"),
        (Decimal("0.0"), "0"),
        (Decimal("0.00075"), "0.00075"),
        (Decimal("1.5000"), "1.5"),
        (Decimal("500"), "500"),
        (Decimal("0.15"), "0.15"),
        (1, "1"),
    ],
)
def test_money_formatting(value: Decimal | int, expected: str) -> None:
    assert money(value) == expected


def test_cached_input_tokens_billed_at_cached_price(price_book: PriceBook) -> None:
    # 60 fresh * 2.5 + 40 cached * 1.25 + 50 out * 10 = 150 + 50 + 500 per 1M
    assert price_book.cost("priced", 100, 50, cached_input_tokens=40) == Decimal("0.0007")


def test_cached_input_without_cached_price_costs_input_price(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text("version: 1\ncurrency: USD\nmodels:\n  m: {input: 2, output: 4}\n")
    book = PriceBook.from_yaml(path)
    assert book.cost("m", 100, 0, cached_input_tokens=100) == book.cost("m", 100, 0)


def test_cached_tokens_cannot_exceed_input(price_book: PriceBook) -> None:
    with pytest.raises(ValueError):
        price_book.cost("priced", 10, 0, cached_input_tokens=11)


def test_provider_field_is_optional(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "version: 1\ncurrency: USD\nmodels:\n"
        "  a: {input: 1, output: 1, provider: azure_openai}\n  b: {input: 1, output: 1}\n"
    )
    book = PriceBook.from_yaml(path)
    assert book.provider_for("a") == "azure_openai"
    assert book.provider_for("b") is None
    with pytest.raises(UnknownModelError):
        book.provider_for("nope")


def test_reasoning_flag_defaults_to_false(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "version: 1\ncurrency: USD\nmodels:\n"
        "  r: {input: 1, output: 1, reasoning: true}\n  c: {input: 1, output: 1}\n"
    )
    book = PriceBook.from_yaml(path)
    assert book.is_reasoning("r") and not book.is_reasoning("c")


# ---- cache writes (ADR-0027) ---------------------------------------------------------------

CLAUDE_BOOK = """\
version: v
currency: USD
models:
  claude: {input: 2, output: 10, cached_input: 0.1, cache_write: 2.5, cache_write_1h: 4}
  plain: {input: 2, output: 10}
"""


@pytest.fixture
def claude_book(tmp_path: Path) -> PriceBook:
    path = tmp_path / "claude.yaml"
    path.write_text(CLAUDE_BOOK)
    return PriceBook.from_yaml(path)


def test_cache_writes_are_billed_at_their_own_prices(claude_book: PriceBook) -> None:
    # 23,120 prompt tokens: 120 fresh, 20,000 read from the cache, 3,000 written to it, of
    # which 1,000 with the one-hour lifetime; 50 output tokens.
    # 120*2 + 20000*0.1 + 2000*2.5 + 1000*4 + 50*10 = 240 + 2000 + 5000 + 4000 + 500 per 1M
    cost = claude_book.cost(
        "claude",
        23_120,
        50,
        cached_input_tokens=20_000,
        cache_write_input_tokens=3_000,
        cache_write_1h_input_tokens=1_000,
    )
    assert cost == Decimal("0.01174")


def test_cache_writes_without_their_own_price_cost_the_input_price(claude_book: PriceBook) -> None:
    with_writes = claude_book.cost(
        "plain", 100, 0, cache_write_input_tokens=60, cache_write_1h_input_tokens=20
    )
    assert with_writes == claude_book.cost("plain", 100, 0)


def test_one_hour_writes_fall_back_to_the_cache_write_price(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        "version: v\ncurrency: USD\nmodels:\n  m: {input: 2, output: 0, cache_write: 3}\n"
    )
    book = PriceBook.from_yaml(path)
    # 100 tokens, all written to the cache, half of them for one hour: all at 3 per 1M
    assert book.cost(
        "m", 100, 0, cache_write_input_tokens=100, cache_write_1h_input_tokens=50
    ) == Decimal("0.0003")


@pytest.mark.parametrize(
    ("cached", "written", "written_1h"),
    [(60, 50, 0), (0, 10, 11), (0, -1, 0)],
)
def test_cache_token_counts_must_fit_the_prompt(
    claude_book: PriceBook, cached: int, written: int, written_1h: int
) -> None:
    with pytest.raises(ValueError):
        claude_book.cost(
            "claude",
            100,
            0,
            cached_input_tokens=cached,
            cache_write_input_tokens=written,
            cache_write_1h_input_tokens=written_1h,
        )


def test_repo_price_book_has_the_claude_rows() -> None:
    book = PriceBook.from_yaml(Path(__file__).parents[1] / "config" / "pricing.yaml")
    for model in ("claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        price = book.price(model)
        assert price.cached_input_per_million is not None
        assert price.cache_write_per_million == price.input_per_million * Decimal("1.25")
        assert price.cache_write_1h_per_million == price.input_per_million * 2


ALIAS_YAML = """\
version: alias-1
currency: USD
models:
  priced: {input: 2.5, output: 10, cached_input: 1.25, provider: azure_openai, reasoning: true}
  plan: {alias_of: priced}
"""


def test_alias_is_priced_and_served_as_its_target(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(ALIAS_YAML)
    book = PriceBook.from_yaml(path)
    assert book.has("plan") and book.is_alias("plan") and not book.is_alias("priced")
    assert book.target("plan") == "priced" and book.target("priced") == "priced"
    assert book.cost("plan", 1000, 100, 400) == book.cost("priced", 1000, 100, 400)
    assert book.provider_for("plan") == "azure_openai" and book.is_reasoning("plan")
    # an alias is a name, not a priced row
    assert book.models() == ["priced"] and book.aliases() == {"plan": "priced"}


@pytest.mark.parametrize(
    "row",
    [
        "plan: {alias_of: nowhere}",  # the target has no price
        "plan: {alias_of: again}\n  again: {alias_of: priced}",  # no chains
        "plan: {alias_of: priced, input: 1, output: 1}",  # an alias has no price of its own
        "plan: {alias_of: priced, use_case: 'not a tag'}",  # a use case is a tag
        "plan: {alias_of: priced}\n  other: {input: 1, output: 1, use_case: plan}",  # alias only
    ],
)
def test_malformed_alias_is_rejected(tmp_path: Path, row: str) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(ALIAS_YAML.replace("plan: {alias_of: priced}", row))
    with pytest.raises(PriceBookError, match="alias_of|alias row"):
        PriceBook.from_yaml(path)


def test_alias_can_carry_a_use_case(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        ALIAS_YAML.replace("plan: {alias_of: priced}", "plan: {alias_of: priced, use_case: plan}")
    )
    book = PriceBook.from_yaml(path)
    assert book.use_case_of("plan") == "plan" and book.target("plan") == "priced"
    # only an alias that names one carries a use case
    assert book.use_case_of("priced") is None and book.use_case_of("unknown") is None
    path.write_text(ALIAS_YAML)
    assert PriceBook.from_yaml(path).use_case_of("plan") is None


# ---- long prompt prices (ADR-0037) ----------------------------------------------------------

LONG_PROMPT_BOOK = """\
version: v
currency: USD
models:
  haiku:
    input: 0.1
    output: 0.5
    cached_input: 0.01
    cache_write: 0.125
    cache_write_1h: 0.2
    long_prompt:
      { above: 100000, input: 0.5, output: 2.5, cached_input: 0.05, cache_write: 0.625,
        cache_write_1h: 1 }
  bare: {input: 1, output: 2, cached_input: 0.1, long_prompt: {above: 1000, input: 3, output: 4}}
  role: {alias_of: haiku}
"""


@pytest.fixture
def long_prompt_book(tmp_path: Path) -> PriceBook:
    path = tmp_path / "long.yaml"
    path.write_text(LONG_PROMPT_BOOK)
    return PriceBook.from_yaml(path)


def test_long_prompt_is_priced_with_its_own_prices(long_prompt_book: PriceBook) -> None:
    # the call that showed the gap (#103): 102,646 prompt tokens, 85,273 of them read from the
    # cache, 586 output tokens. (17373*0.5 + 85273*0.05 + 586*2.5) / 1e6
    cost = long_prompt_book.cost("haiku", 102_646, 586, cached_input_tokens=85_273)
    assert cost == Decimal("0.01441515")
    # an alias is priced as its target, long prompt included
    assert long_prompt_book.cost("role", 102_646, 586, cached_input_tokens=85_273) == cost
    # cache writes of a long prompt: (1000*0.5 + 150000*0.625 + 50000*1) / 1e6
    written = long_prompt_book.cost(
        "haiku", 201_000, 0, cache_write_input_tokens=200_000, cache_write_1h_input_tokens=50_000
    )
    assert written == Decimal("0.14425")


def test_prompt_at_the_threshold_keeps_the_base_prices(long_prompt_book: PriceBook) -> None:
    assert long_prompt_book.cost("haiku", 100_000, 1_000) == Decimal("0.0105")
    # one token more and the whole call, output included, is priced as a long prompt
    assert long_prompt_book.cost("haiku", 100_001, 1_000) == Decimal("0.0525005")


def test_long_prompt_without_cache_prices_falls_back_to_its_own_input(
    long_prompt_book: PriceBook,
) -> None:
    # the long prompt names no cache prices: cached and written tokens cost its input price,
    # not the base row's cached_input
    cost = long_prompt_book.cost(
        "bare", 2_000, 0, cached_input_tokens=1_000, cache_write_input_tokens=500
    )
    assert cost == Decimal("0.006")


@pytest.mark.parametrize(
    "long_prompt",
    [
        "5",  # not a mapping
        "{input: 3, output: 4}",  # no threshold
        "{above: 0, input: 3, output: 4}",
        "{above: 1.5, input: 3, output: 4}",
        "{above: true, input: 3, output: 4}",
        "{above: 1000, input: 3}",  # no output price
        "{above: 1000, output: 4}",  # no input price
        "{above: 1000, input: 3, output: 4, cached: 1}",  # unknown key
    ],
)
def test_malformed_long_prompt_is_rejected(tmp_path: Path, long_prompt: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "version: v\ncurrency: USD\nmodels:\n"
        f"  m: {{input: 1, output: 2, long_prompt: {long_prompt}}}\n"
    )
    with pytest.raises(PriceBookError, match="long_prompt"):
        PriceBook.from_yaml(path)


def test_repo_price_book_has_the_long_prompt_prices_of_haiku() -> None:
    book = PriceBook.from_yaml(Path(__file__).parents[1] / "config" / "pricing.yaml")
    base = book.price("claude-haiku-5-5")
    long = base.long_prompt
    assert long is not None and long.above == 100_000
    assert long.input_per_million == base.input_per_million * 5
    assert long.output_per_million == base.output_per_million * 5
    assert base.cached_input_per_million is not None
    assert long.cached_input_per_million == base.cached_input_per_million * 5
    assert long.cache_write_per_million == long.input_per_million * Decimal("1.25")
    assert long.cache_write_1h_per_million == long.input_per_million * 2
    assert book.price("claude-sonnet-5-5").long_prompt is None
