# 0037. A price book row can price a long prompt

Date: 2026-11-20
Status: Accepted

## Context

A price book row has one set of prices. Some models have two. `claude-haiku-5-5` costs
0.10 / 0.50 USD per 1M tokens when the prompt is 100K tokens or fewer and 0.50 / 2.50 above
that, cache reads and writes included. The shipped price book said so in a comment and priced
every call at the lower set.

The gap showed on the first call that crossed the line (#103). VS Code behind the LiteLLM
plugin sent a prompt of 102,646 tokens, 85,273 of them read from the cache, and got 586 output
tokens back. The ledger recorded 0.00288303 USD, LiteLLM's spend log 0.01441515. Nine other
calls of the same run were equal on both sides. An editor that sends about 90K tokens of tool
definitions with every call crosses the line after a few turns, so for that traffic the
under-count is the common case, and the budgets count the same fifth of the cost.

The shape is not Anthropic's alone. LiteLLM's price map (1.108.0) carries a higher price above
272K prompt tokens for the GPT-5.4 family on Azure and OpenAI, and above 200K for Gemini and
xAI models.

## Decision

- **A priced row may carry `long_prompt`:** a threshold and a second set of prices.

  ```yaml
  claude-haiku-5-5:
    input: 0.10
    output: 0.50
    cached_input: 0.01
    cache_write: 0.125
    cache_write_1h: 0.20
    long_prompt: { above: 100000, input: 0.50, output: 2.50, cached_input: 0.05, cache_write: 0.625, cache_write_1h: 1.00 }
  ```

- **A call whose prompt is longer than `above` is priced with that entry as a whole,** output
  included. At `above` tokens or fewer nothing changes. The prompt is `input_tokens`: tokens
  read from the cache and written to it count.
- **The entry's cache prices fall back as on a row, to its own prices:** no `cached_input` or
  `cache_write` means its `input`, no `cache_write_1h` means its `cache_write`. It never falls
  back to the row's lower prices.
- **The reservation follows the same rule** on the estimated prompt, because the estimate and
  the settlement call the same function.
- **A malformed entry fails the price book load:** no `above`, `input` or `output`, an `above`
  that is not a whole number of 1 or more, or a key the entry does not take. An alias row
  takes no `long_prompt`; it is priced as its target (ADR-0033).
- **One entry per row.** No model in the price book needs a third set of prices.
- **The report's price book view lists the entry** as a second line under the model, with the
  threshold in its name, so the currency switch and the sorting apply to it.
- Gateway and plugin, every route. No schema revision: `input_tokens` and
  `price_book_version` on the ledger row are enough to reproduce the price.

Considered and not chosen:

- A second row under another name (`claude-haiku-5-5-long`). The client does not know the
  prompt's length in tokens before the call, and every allow-list and model budget would need
  both names.
- Pricing only the tokens above the threshold at the higher prices. That is not how the call
  above was billed: all of its tokens were priced with the second set.
- Reading LiteLLM's price map at request time in the plugin. The price book is a reviewed file
  in git with a version on every ledger row; a lookup would price the same call differently in
  the gateway and in the plugin, and after a LiteLLM upgrade.
- A list of thresholds. It can replace the single entry when a model needs it.

## Consequences

- The call of #103 is priced at 0.01441515 USD, the amount in LiteLLM's spend log; a test in
  `tests/test_pricing.py` and one in `tests/test_litellm_plugin.py` hold that number.
- A call's cost steps at the threshold: on Haiku 100,001 prompt tokens cost five times what
  100,000 cost. Nothing on the ledger row says which set priced it; `input_tokens` does.
- The estimate has no tokenizer (prompt characters / 4), so a prompt near the threshold can be
  reserved on one side of it and settled on the other. Settlement uses the provider's count
  and corrects the counters; until then the reservation of that one call is off by the ratio of
  the two sets, as any estimate can be off.
- Rows recorded before the price book carried the entry keep their cost and their price book
  version. Long-prompt calls among them stay under-counted in the ledger; `ledger reconcile`
  is the place where that shows.
- Whether a provider counts cached tokens toward its threshold is taken from LiteLLM's price
  map, which counts them. It was not checked against a provider's invoice.
- `pricing sync` (ADR-0013) compares the row's own prices with Azure's meters. It does not
  look at `long_prompt`.
