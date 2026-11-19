"""Draws the two request-path diagrams of the README: architecture.svg / .png (the gateway)
and architecture-litellm.svg / .png (the LiteLLM plugin).

    pip install -e ".[demo]"        # matplotlib
    python docs/images/diagrams.py

Coordinates are pixels of the 2000 x 1080 PNG, origin top left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

W, H = 2000, 1080
INK, MUTED, BLUE, RED = "#1c1c1c", "#555555", "#1f6fd6", "#cc2222"
PAPER, EDGE, GREY_ROW, BLUE_ROW = "#fafaf8", "#b9b9b6", "#f1f1ee", "#e9effa"
FONT = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
PX = 72 / 200  # font sizes below are pixel heights; the figure is drawn at 200 dpi


@dataclass(frozen=True)
class Diagram:
    name: str
    title: str
    subtitle: str
    core_title: str
    core_subtitle: str
    rows: list[tuple[str, str, str]]  # label, what happens, status code when it fails
    footer: str
    app: tuple[str, str]  # the two lines under "Application"
    sends: tuple[str, str]  # labels above and below the arrow into the core
    provider: str
    boxes: list[tuple[str, str, str]] = field(default_factory=list)  # config, ledger, reports


GATEWAY = Diagram(
    name="architecture",
    title="llm-finops-gateway: request path",
    subtitle="Fail closed before money is spent; attribute and price after. "
    "No prompt text is stored.",
    core_title="Gateway (FastAPI)",
    core_subtitle="POST /v1/chat/completions",
    rows=[
        ("1 Auth + owner", "team key, X-Project, X-Use-Case", "401 / 400"),
        ("2 Price book", "unknown model fails", "422"),
        ("3 Policy", "registry, allow-list, rate, reserve", "403 / 429"),
        ("4 Provider call", "Ollama, Azure, compatible", "502"),
        ("5 Price + ledger", "tokens x versioned price, one row per call", ""),
    ],
    footer="6 Response: OpenAI shape or SSE + x_finops block + X-FinOps-* headers",
    app=("OpenAI SDK, LangChain", "base_url = gateway"),
    sends=("Bearer key", "X-Project"),
    provider="Azure | Ollama | OpenAI-compatible",
    boxes=[
        (
            "config/ (git)",
            "pricing.yaml, versioned, hot reload",
            "policies.yaml, keys.yaml (hashes)",
        ),
        ("Ledger", "SQLite / PostgreSQL", "team, project, model, tokens, cost"),
        ("Chargeback", "/finops, JSON + CSV, FOCUS export", "/metrics, OTel, webhook alerts"),
    ],
)

LITELLM = Diagram(
    name="architecture-litellm",
    title="The same rules inside LiteLLM: the FinOps plugin",
    subtitle="LiteLLM stays the proxy; callback hooks add attribution, a price book, budgets, "
    "a ledger and the report. No second proxy.",
    core_title="LiteLLM proxy",
    core_subtitle="/v1/chat/completions, /v1/messages, /v1/responses; the plugin is a callback",
    rows=[
        ("pre-call hook", "team from the key; project and use case", "400"),
        ("", "price book: unknown model fails", "422"),
        ("", "registry, allow-lists, rate limit, reserve", "403 / 429"),
        ("LiteLLM routes", "Azure OpenAI, Anthropic, 100+ providers", ""),
        ("settle", "real usage, cache reads and writes; X-FinOps-* headers", ""),
    ],
    footer="failure hook: the reservation is released, nothing is billed",
    app=("Claude Code, Copilot, SDKs", "base_url = LiteLLM"),
    sends=("virtual key", "X-Project"),
    provider="Azure | OpenAI | Anthropic | ...",
    boxes=[
        (
            "Config files (git)",
            "the gateway's price book and policies",
            "hot reload; mounted as a directory",
        ),
        ("Ledger", "the gateway's tables", "team, project, use case, model, cost"),
        ("Reports", "/finops inside the LiteLLM dashboard", "FOCUS export, /metrics/, alerts"),
    ],
)


def _box(ax: Axes, x: float, y: float, w: float, h: float, **style: object) -> None:
    ax.add_patch(
        FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=10", **style)  # type: ignore[arg-type]
    )


def _arrow(ax: Axes, start: tuple[float, float], end: tuple[float, float], color: str) -> None:
    ax.add_patch(
        FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=22, lw=2.2, color=color)
    )


def _card(ax: Axes, x: float, y: float, w: float, h: float, lines: tuple[str, ...]) -> None:
    """A white box with a bold first line and muted lines under it, all centred."""
    _box(ax, x, y, w, h, facecolor=PAPER, edgecolor=EDGE, lw=1.6)
    cx = x + w / 2
    ax.text(cx, y + 34, lines[0], ha="center", va="center", fontsize=PX * 31, weight="bold")
    for i, line in enumerate(lines[1:]):
        ax.text(cx, y + 66 + 27 * i, line, ha="center", va="center", fontsize=PX * 23, color=MUTED)


def draw(d: Diagram, out: Path) -> None:
    plt.rcParams["font.family"] = FONT
    fig = plt.figure(figsize=(W / 200, H / 200), dpi=200, facecolor=PAPER)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    ax.text(80, 72, d.title, fontsize=PX * 56, weight="bold", color=INK, va="center")
    ax.text(80, 119, d.subtitle, fontsize=PX * 30, color=MUTED, va="center")

    # the proxy with its steps
    _box(ax, 440, 220, 1000, 590, facecolor=PAPER, edgecolor=BLUE, lw=3)
    ax.text(480, 260, d.core_title, fontsize=PX * 42, weight="bold", color=BLUE, va="center")
    ax.text(480, 293, d.core_subtitle, fontsize=PX * 27, color=MUTED, va="center")
    for i, (label, what, code) in enumerate(d.rows):
        y = 326 + 76 * i
        _box(ax, 478, y, 924, 66, facecolor=GREY_ROW if code else BLUE_ROW, edgecolor="none")
        ax.text(498, y + 33, label, fontsize=PX * 25, color=INK, va="center")
        ax.text(720, y + 33, what, fontsize=PX * 25, color=INK, va="center")
        ax.text(
            1382, y + 33, code, fontsize=PX * 25, weight="bold", color=RED, va="center", ha="right"
        )
    ax.text(480, 780, d.footer, fontsize=PX * 27, color=MUTED, va="center")

    # the caller
    _card(ax, 20, 430, 310, 160, ("Application", *d.app))
    _arrow(ax, (330, 510), (440, 510), INK)
    ax.text(385, 482, d.sends[0], fontsize=PX * 18, color=MUTED, ha="center", va="center")
    ax.text(385, 538, d.sends[1], fontsize=PX * 18, color=MUTED, ha="center", va="center")

    # what leaves the proxy
    _arrow(ax, (1440, 440), (1500, 440), RED)
    ax.text(1515, 424, "rejected before", fontsize=PX * 28, weight="bold", color=RED, va="center")
    ax.text(1515, 460, "any spend", fontsize=PX * 28, weight="bold", color=RED, va="center")
    _card(ax, 1500, 540, 440, 120, ("LLM provider", d.provider))
    _arrow(ax, (1440, 600), (1500, 600), INK)

    # files in, ledger out, reports from the ledger
    config, ledger, reports = d.boxes
    _card(ax, 440, 870, 440, 150, config)
    _arrow(ax, (660, 870), (660, 810), INK)
    _card(ax, 980, 870, 440, 150, ledger)
    _arrow(ax, (1200, 810), (1200, 870), INK)
    _card(ax, 1500, 870, 440, 150, reports)
    _arrow(ax, (1420, 945), (1500, 945), INK)

    fig.savefig(out / f"{d.name}.svg")
    fig.savefig(out / f"{d.name}.png")
    plt.close(fig)


if __name__ == "__main__":
    here = Path(__file__).parent
    for diagram in (GATEWAY, LITELLM):
        draw(diagram, here)
        print(f"wrote {diagram.name}.svg and .png")
