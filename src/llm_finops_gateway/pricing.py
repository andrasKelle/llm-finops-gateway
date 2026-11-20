"""Versioned price book.

Design decision (ADR-0002): a model without a configured price raises ``UnknownModelError``.
Silently costing zero would hide spend, which is the exact failure a FinOps gateway exists
to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

# the shape of a use case name, as on X-Use-Case
_USE_CASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class LongPromptPrice:
    """The prices of a call whose prompt is longer than ``above`` tokens (ADR-0037). A cache
    price left out falls back as on the row itself, to this entry's own prices."""

    above: int
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    cache_write_per_million: Decimal | None = None
    cache_write_1h_per_million: Decimal | None = None


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None  # None: cached tokens cost the input price
    # Prompt tokens written to the provider's cache (Anthropic): the 5-minute and the 1-hour
    # lifetime. None: the input price, and the 1-hour price falls back to the 5-minute one.
    cache_write_per_million: Decimal | None = None
    cache_write_1h_per_million: Decimal | None = None
    provider: str | None = None  # adapter that serves the model; None: the default provider
    reasoning: bool = False  # OpenAI reasoning family: max_completion_tokens, fixed temperature
    long_prompt: LongPromptPrice | None = None  # a second set of prices for a long prompt


class UnknownModelError(KeyError):
    def __init__(self, model: str) -> None:
        super().__init__(f"No price configured for model '{model}'")
        self.model = model

    def __str__(self) -> str:
        return f"No price configured for model '{self.model}'"


class PriceBookError(ValueError):
    """Raised when the price book file is malformed."""


def money(value: Decimal | int | str) -> str:
    """Canonical string for a USD amount: no exponent, no trailing zeros ('0', '0.00075')."""
    d = Decimal(str(value)).normalize()
    if d == d.to_integral():
        return str(d.quantize(Decimal(1)))
    return format(d, "f")


_LONG_PROMPT_KEYS = {"above", "input", "output", "cached_input", "cache_write", "cache_write_1h"}


def _optional(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _long_prompt(path: Path, name: str, spec: Any) -> LongPromptPrice:
    where = f"{path}: model '{name}': long_prompt"
    if not isinstance(spec, dict) or "input" not in spec or "output" not in spec:
        raise PriceBookError(f"{where} needs 'above', 'input' and 'output'")
    if unknown := set(spec) - _LONG_PROMPT_KEYS:
        raise PriceBookError(f"{where} does not take {sorted(unknown)}")
    above = spec.get("above")
    if not isinstance(above, int) or isinstance(above, bool) or above < 1:
        raise PriceBookError(f"{where}: 'above' must be a number of prompt tokens, 1 or more")
    return LongPromptPrice(
        above=above,
        input_per_million=Decimal(str(spec["input"])),
        output_per_million=Decimal(str(spec["output"])),
        cached_input_per_million=_optional(spec.get("cached_input")),
        cache_write_per_million=_optional(spec.get("cache_write")),
        cache_write_1h_per_million=_optional(spec.get("cache_write_1h")),
    )


class PriceBook:
    """Immutable, versioned price list loaded from YAML."""

    def __init__(
        self,
        prices: dict[str, ModelPrice],
        version: str,
        currency: str,
        aliases: dict[str, str] | None = None,
        alias_use_cases: dict[str, str] | None = None,
    ) -> None:
        self._prices = dict(prices)
        # alias -> priced row: a name clients call that is priced as its target (ADR-0033)
        self._aliases = dict(aliases or {})
        # alias -> the use case a call to that name belongs to (ADR-0036)
        self._alias_use_cases = dict(alias_use_cases or {})
        self.version = version
        self.currency = currency

    @classmethod
    def from_yaml(cls, path: Path) -> PriceBook:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise PriceBookError(f"{path}: top level must be a mapping")
        for key in ("version", "currency", "models"):
            if key not in raw:
                raise PriceBookError(f"{path}: missing required key '{key}'")
        if not isinstance(raw["models"], dict):
            raise PriceBookError(f"{path}: 'models' must be a mapping")
        currency = str(raw["currency"])
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise PriceBookError(f"{path}: 'currency' must be a 3-letter code like USD or EUR")
        prices: dict[str, ModelPrice] = {}
        aliases: dict[str, str] = {}
        alias_use_cases: dict[str, str] = {}
        for name, spec in raw["models"].items():
            if isinstance(spec, dict) and "alias_of" in spec:
                if set(spec) - {"alias_of", "use_case"}:
                    raise PriceBookError(
                        f"{path}: alias row '{name}' takes only 'alias_of' and 'use_case'"
                    )
                aliases[str(name)] = str(spec["alias_of"])
                if "use_case" in spec:
                    use_case = str(spec["use_case"])
                    if not _USE_CASE_RE.fullmatch(use_case):
                        raise PriceBookError(
                            f"{path}: alias row '{name}': use_case must match "
                            f"{_USE_CASE_RE.pattern}"
                        )
                    alias_use_cases[str(name)] = use_case
                continue
            if not isinstance(spec, dict) or "input" not in spec or "output" not in spec:
                raise PriceBookError(f"{path}: model '{name}' needs 'input' and 'output'")
            if "use_case" in spec:
                raise PriceBookError(
                    f"{path}: model '{name}': 'use_case' belongs on an alias row (alias_of)"
                )
            cached = spec.get("cached_input")
            write, write_1h = spec.get("cache_write"), spec.get("cache_write_1h")
            provider = spec.get("provider")
            prices[str(name)] = ModelPrice(
                input_per_million=Decimal(str(spec["input"])),
                output_per_million=Decimal(str(spec["output"])),
                cached_input_per_million=None if cached is None else Decimal(str(cached)),
                cache_write_per_million=None if write is None else Decimal(str(write)),
                cache_write_1h_per_million=None if write_1h is None else Decimal(str(write_1h)),
                provider=None if provider is None else str(provider),
                reasoning=bool(spec.get("reasoning", False)),
                long_prompt=(
                    _long_prompt(path, str(name), spec["long_prompt"])
                    if "long_prompt" in spec
                    else None
                ),
            )
        for name, target in aliases.items():
            if target not in prices:
                raise PriceBookError(
                    f"{path}: model '{name}': alias_of must name a priced row, got '{target}'"
                )
        return cls(
            prices,
            version=str(raw["version"]),
            currency=currency,
            aliases=aliases,
            alias_use_cases=alias_use_cases,
        )

    def models(self) -> list[str]:
        """The priced rows; aliases are in :meth:`aliases`."""
        return sorted(self._prices)

    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def is_alias(self, model: str) -> bool:
        return model in self._aliases

    def target(self, model: str) -> str:
        """The priced row behind ``model``: itself, or the row an alias points at."""
        return self._aliases.get(model, model)

    def use_case_of(self, model: str) -> str | None:
        """The use case an alias row names (ADR-0036); None for every other name."""
        return self._alias_use_cases.get(model)

    def has(self, model: str) -> bool:
        return self.target(model) in self._prices

    def price(self, model: str) -> ModelPrice:
        try:
            return self._prices[self.target(model)]
        except KeyError as exc:
            raise UnknownModelError(model) from exc

    def is_reasoning(self, model: str) -> bool:
        """True for rows flagged ``reasoning: true`` (gpt-5, o-series): the upstream takes
        ``max_completion_tokens`` instead of ``max_tokens`` and only the default temperature."""
        try:
            return self._prices[self.target(model)].reasoning
        except KeyError as exc:
            raise UnknownModelError(model) from exc

    def provider_for(self, model: str) -> str | None:
        """Provider named by the price book row, or None for the default (ADR-0011)."""
        try:
            return self._prices[self.target(model)].provider
        except KeyError as exc:
            raise UnknownModelError(model) from exc

    def cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_input_tokens: int = 0,
        cache_write_1h_input_tokens: int = 0,
    ) -> Decimal:
        """Cost of one call. ``input_tokens`` is the whole prompt. Of it,
        ``cached_input_tokens`` were served from the provider's prompt cache (billed at
        ``cached_input``) and ``cache_write_input_tokens`` were written to it (billed at
        ``cache_write``); ``cache_write_1h_input_tokens`` is the part of the written tokens
        with the one-hour lifetime (billed at ``cache_write_1h``). The rest costs ``input``.
        A row with ``long_prompt`` prices the whole call with that entry when ``input_tokens``
        is more than its ``above`` (ADR-0037)."""
        counts = (
            input_tokens,
            output_tokens,
            cached_input_tokens,
            cache_write_input_tokens,
            cache_write_1h_input_tokens,
        )
        if min(counts) < 0:
            raise ValueError("token counts must be non-negative")
        if cached_input_tokens + cache_write_input_tokens > input_tokens:
            raise ValueError("cached and cache-write tokens cannot exceed input_tokens")
        if cache_write_1h_input_tokens > cache_write_input_tokens:
            raise ValueError("cache_write_1h_input_tokens cannot exceed cache_write_input_tokens")
        try:
            row = self._prices[self.target(model)]
        except KeyError as exc:
            raise UnknownModelError(model) from exc
        price: ModelPrice | LongPromptPrice = row
        if row.long_prompt is not None and input_tokens > row.long_prompt.above:
            price = row.long_prompt
        cached_price = (
            price.input_per_million
            if price.cached_input_per_million is None
            else price.cached_input_per_million
        )
        write_price = (
            price.input_per_million
            if price.cache_write_per_million is None
            else price.cache_write_per_million
        )
        write_1h_price = (
            write_price
            if price.cache_write_1h_per_million is None
            else price.cache_write_1h_per_million
        )
        fresh = input_tokens - cached_input_tokens - cache_write_input_tokens
        per_million = Decimal(1_000_000)
        return (
            Decimal(fresh) * price.input_per_million
            + Decimal(cached_input_tokens) * cached_price
            + Decimal(cache_write_input_tokens - cache_write_1h_input_tokens) * write_price
            + Decimal(cache_write_1h_input_tokens) * write_1h_price
            + Decimal(output_tokens) * price.output_per_million
        ) / per_million
