"""Графики для писем и PDF (matplotlib -> PNG в памяти). Цвета проверены на различимость, подписи дублируют цвет."""
from __future__ import annotations

import io
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from app.analytics import EmployeeRow  # noqa: E402
from app.labels import MOOD_LABELS, REASON_LABELS, stage_label  # noqa: E402
from app.models import MOODS  # noqa: E402

INK, INK2, GRID = "#1b2733", "#5b6775", "#e3e7eb"
BLUE = "#2a78d6"
MOOD_COLORS = {"positive": "#1baf7a", "neutral": "#aab3bd", "negative": "#eb8a34", "critical": "#d23c3c"}
DPI, W = 200, 7.2

for _f in ("DejaVu Sans", "Liberation Sans", "Arial"):
    if any(x.name == _f for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams.update({"axes.edgecolor": GRID, "text.color": INK, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK, "font.size": 10})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor="white", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return buf.getvalue()


def _clean(ax, keep_bottom=False):
    for side in ("top", "right", "left") + (() if keep_bottom else ("bottom",)):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


def mood_donut(moods: dict) -> Optional[bytes]:
    vals = [moods.get(m, 0) for m in MOODS]
    total = sum(vals)
    if not total:
        return None
    fig, ax = plt.subplots(figsize=(W * .62, 2.9))
    nz = [(m, v) for m, v in zip(MOODS, vals) if v]
    ax.pie([v for _, v in nz], colors=[MOOD_COLORS[m] for m, _ in nz], startangle=90, counterclock=False,
           wedgeprops={"width": .36, "edgecolor": "white", "linewidth": 2})
    ax.text(0, .08, str(total), ha="center", va="center", fontsize=22, fontweight="bold", color=INK)
    ax.text(0, -.2, "сообщений", ha="center", va="center", fontsize=9, color=INK2)
    handles = [plt.Line2D([], [], marker="o", ls="", color=MOOD_COLORS[m], markersize=9,
                          label=f"{MOOD_LABELS[m]} — {moods.get(m, 0)} ({moods.get(m, 0) / total:.0%})") for m in MOODS]
    leg = ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.0, .5), frameon=False, fontsize=9.5)
    for t in leg.get_texts():
        t.set_color(INK)
    ax.set_aspect("equal")
    return _png(fig)


def hbar(items: list[tuple[str, float]], color: str = BLUE, fmt=lambda v: f"{v:g}") -> Optional[bytes]:
    items = [(k, v) for k, v in items if v is not None]
    if not items or not any(v for _, v in items):
        return None
    labels, vals = zip(*items)
    fig, ax = plt.subplots(figsize=(W, .42 * len(items) + .5))
    y = range(len(items))
    ax.barh(list(y), vals, height=.55, color=color)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.set_xticks([])
    mx = max(vals)
    for i, v in enumerate(vals):
        ax.text(v + mx * .015, i, fmt(v), va="center", fontsize=9.5, color=INK)
    ax.set_xlim(0, mx * 1.12)
    _clean(ax)
    return _png(fig)


def reasons_chart(reasons: dict) -> Optional[bytes]:
    items = sorted(((REASON_LABELS.get(k, k), v) for k, v in reasons.items() if v), key=lambda x: -x[1])
    return hbar(items, fmt=lambda v: f"{int(v)}")


def stage_chart(series: list[tuple[Optional[str], int]]) -> Optional[bytes]:
    return hbar([(stage_label(s), c) for s, c in series], fmt=lambda v: f"{int(v)}")


def _stacked(names: list[str], moods_list: list[dict], side_notes: list[str], height_per=.42) -> Optional[bytes]:
    if not names:
        return None
    fig, ax = plt.subplots(figsize=(W, height_per * len(names) + .9))
    for i, moods in enumerate(moods_list):
        total = sum(moods.get(m, 0) for m in MOODS)
        if not total:
            ax.barh(i, 100, height=.55, color="#f1f3f5")
            ax.text(2, i, "нет обращений", va="center", fontsize=8.5, color=INK2)
            continue
        left = 0
        for m in MOODS:
            share = moods.get(m, 0) / total * 100
            if share:
                ax.barh(i, share, left=left, height=.55, color=MOOD_COLORS[m], edgecolor="white", linewidth=1.5)
                if share >= 9:
                    ax.text(left + share / 2, i, f"{share:.0f}%", ha="center", va="center", fontsize=8,
                            color="white" if m != "neutral" else INK, fontweight="bold")
            left += share
    ax.set_yticks(range(len(names)), names)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xticks([])
    for i, note in enumerate(side_notes):
        ax.text(101.5, i, note, va="center", fontsize=8.5, color=INK2)
    handles = [plt.Line2D([], [], marker="s", ls="", color=MOOD_COLORS[m], markersize=9, label=MOOD_LABELS[m]) for m in MOODS]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(.5, 1.0), ncol=4, frameon=False, fontsize=9)
    _clean(ax)
    return _png(fig)


def employees_mood_chart(rows: list[EmployeeRow], limit: int = 15) -> Optional[bytes]:
    rows = rows[:limit]
    return _stacked([r.person.short_name for r in rows], [r.moods for r in rows],
                    [f"n={r.total}" if r.total else "" for r in rows])


def groups_mood_chart(groups: dict[str, dict]) -> Optional[bytes]:
    names = sorted(groups, key=lambda k: -groups[k]["neg_share"])
    return _stacked(names, [groups[k]["moods"] for k in names],
                    [f"{groups[k]['people']} чел." for k in names])


def trend_chart(trend: list[dict]) -> Optional[bytes]:
    pts = [(i, t["index"]) for i, t in enumerate(trend) if t["index"] is not None]
    if len(pts) < 2:
        return None
    fig, ax = plt.subplots(figsize=(W, 2.6))
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=BLUE, linewidth=2, marker="o", markersize=7, markeredgecolor="white", markeredgewidth=2)
    ax.axhline(60, color="#c3cad2", linewidth=1, linestyle=(0, (4, 3)), zorder=0)
    ax.set_ylim(0, 100)
    ax.set_xlim(-.5, len(trend) - .5)
    ax.set_xticks(range(len(trend)), [t["week"].strftime("%d.%m") for t in trend])
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(axis="y", color=GRID, linewidth=.8)
    ax.set_axisbelow(True)
    ax.annotate(f"{ys[-1]:.0f}", (xs[-1], ys[-1]), textcoords="offset points", xytext=(0, 10), ha="center",
                fontsize=10, fontweight="bold", color=INK)
    _clean(ax, keep_bottom=True)
    ax.spines["bottom"].set_color(GRID)
    return _png(fig)
