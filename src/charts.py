"""Rate trend chart — generates a combined PNG embedded in the email.

Layout:
  Left (65%): 6-month monthly trend line
  Right (35%): 4-week zoom bar chart

Returns base64-encoded PNG so it can be inlined as <img src="data:image/png;base64,...">
in the HTML email with no external hosting needed.
"""

import base64
import io
from collections import defaultdict
from datetime import date, datetime


def _parse_history(history: list[dict]) -> list[dict]:
    entries = []
    for h in history:
        arm = h.get("rate_5_1_arm")
        d = h.get("date")
        if arm and d:
            try:
                entries.append({
                    "date": datetime.strptime(d, "%Y-%m-%d").date(),
                    "arm": float(arm),
                    "fixed30": float(h["rate_30yr_fixed"]) if h.get("rate_30yr_fixed") else None,
                })
            except (ValueError, TypeError):
                continue
    return sorted(entries, key=lambda x: x["date"])


def _monthly_averages(entries: list[dict]) -> list[dict]:
    """Collapse weekly entries into monthly averages, capped at last 6 months."""
    monthly: dict = defaultdict(list)
    for e in entries:
        key = (e["date"].year, e["date"].month)
        monthly[key].append(e["arm"])
    result = []
    for (year, month), rates in sorted(monthly.items()):
        result.append({
            "date": date(year, month, 15),
            "arm": round(sum(rates) / len(rates), 3),
        })
    return result[-6:]


def _to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    buf.seek(0)
    data = buf.read()
    buf.close()
    return data


def generate_chart(history: list[dict], current_rate: float) -> bytes | None:
    """Return raw PNG bytes, or None if matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.gridspec import GridSpec
    except ImportError:
        return None

    entries = _parse_history(history)
    if not entries:
        return None

    monthly = _monthly_averages(entries)
    weekly = entries[-4:]

    # ── Palette (matches email design) ────────────────────────
    NAVY    = "#0f172a"
    BLUE    = "#3b82f6"
    RED     = "#ef4444"
    GREEN   = "#22c55e"
    LGRAY   = "#e2e8f0"
    MGRAY   = "#94a3b8"
    BG      = "#f8fafc"

    fig = plt.figure(figsize=(9.2, 3.0), facecolor="white",
                     layout="constrained")
    gs  = GridSpec(1, 2, figure=fig, width_ratios=[2.1, 1.0], wspace=0.38)

    # ── LEFT: 6-month monthly line chart ──────────────────────
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG)

    if len(monthly) >= 2:
        xs = [e["date"] for e in monthly]
        ys = [e["arm"] for e in monthly]

        ax1.plot(xs, ys, color=BLUE, linewidth=2.2, marker="o",
                 markersize=5, markerfacecolor="white", markeredgecolor=BLUE,
                 markeredgewidth=1.8, zorder=3)
        ax1.fill_between(xs, ys, min(ys) - 0.5,
                         alpha=0.07, color=BLUE, zorder=1)

        # Annotate first and last points
        ax1.annotate(f"{ys[0]:.2f}%", xy=(xs[0], ys[0]),
                     xytext=(-4, 7), textcoords="offset points",
                     fontsize=7.5, color=MGRAY)
        ax1.annotate(f"{ys[-1]:.2f}%", xy=(xs[-1], ys[-1]),
                     xytext=(5, 4), textcoords="offset points",
                     fontsize=8, color=NAVY, fontweight="bold")

        y_vals = ys + [current_rate]
        spread = max(max(y_vals) - min(y_vals), 0.3)
        ax1.set_ylim(min(y_vals) - spread * 0.5, max(y_vals) + spread * 0.7)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        ax1.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)
    elif len(monthly) == 1:
        ax1.text(0.5, 0.5, "Accumulating data…\nCheck back next week.",
                 ha="center", va="center", transform=ax1.transAxes,
                 fontsize=9, color=MGRAY)

    # Your current rate reference line (both charts)
    if monthly:
        ax1.axhline(y=current_rate, color=RED, linestyle="--",
                    linewidth=1.4, alpha=0.75, zorder=2)
        xlim = ax1.get_xlim()
        ax1.text(xlim[0], current_rate + 0.015,
                 f"  Your rate: {current_rate}%",
                 va="bottom", fontsize=7.5, color=RED, alpha=0.85)

    month_label = f"Monthly Trend — last {len(monthly)} month{'s' if len(monthly) != 1 else ''}"
    ax1.set_title(month_label, fontsize=9.5, fontweight="bold", color=NAVY, pad=9)
    ax1.tick_params(axis="both", labelsize=8, colors=MGRAY, length=3)
    ax1.spines[["top", "right"]].set_visible(False)
    ax1.spines["left"].set_color(LGRAY)
    ax1.spines["bottom"].set_color(LGRAY)
    ax1.grid(axis="y", color=LGRAY, linewidth=0.8, zorder=0)
    ax1.set_ylabel("5/1 ARM rate (%)", fontsize=8, color=MGRAY)

    # ── RIGHT: 4-week zoom bar chart ──────────────────────────
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor(BG)

    if weekly:
        labels  = [e["date"].strftime("%-m/%-d") for e in weekly]
        rates_w = [e["arm"] for e in weekly]

        bar_colors = []
        for i, r in enumerate(rates_w):
            if i == 0:
                bar_colors.append(BLUE)
            elif r < rates_w[i - 1] - 0.001:
                bar_colors.append(GREEN)
            elif r > rates_w[i - 1] + 0.001:
                bar_colors.append(RED)
            else:
                bar_colors.append(BLUE)

        bars = ax2.bar(labels, rates_w, color=bar_colors,
                       width=0.52, alpha=0.82, zorder=3,
                       edgecolor="white", linewidth=0.8)

        for bar, rate in zip(bars, rates_w):
            ax2.text(bar.get_x() + bar.get_width() / 2,
                     bar.get_height() + 0.008,
                     f"{rate:.2f}%",
                     ha="center", va="bottom",
                     fontsize=7.5, fontweight="bold", color=NAVY)

        ax2.axhline(y=current_rate, color=RED, linestyle="--",
                    linewidth=1.4, alpha=0.75, zorder=2)

        all_vals = rates_w + [current_rate]
        spread = max(max(all_vals) - min(all_vals), 0.15)
        ax2.set_ylim(min(all_vals) - spread * 0.7,
                     max(all_vals) + spread * 1.4)

        # Week-over-week delta annotation above title
        if len(rates_w) >= 2:
            delta = rates_w[-1] - rates_w[-2]
            sign = "▼" if delta < 0 else "▲" if delta > 0 else "─"
            color = GREEN if delta < 0 else RED if delta > 0 else MGRAY
            ax2.set_title(
                f"4-Week Zoom   {sign} {abs(delta):.2f}% WoW",
                fontsize=9.5, fontweight="bold", color=color, pad=9
            )
        else:
            ax2.set_title("4-Week Zoom", fontsize=9.5,
                          fontweight="bold", color=NAVY, pad=9)
    else:
        ax2.text(0.5, 0.5, "No weekly data yet.",
                 ha="center", va="center", transform=ax2.transAxes,
                 fontsize=9, color=MGRAY)
        ax2.set_title("4-Week Zoom", fontsize=9.5,
                      fontweight="bold", color=NAVY, pad=9)

    ax2.tick_params(axis="both", labelsize=8, colors=MGRAY, length=3)
    ax2.spines[["top", "right"]].set_visible(False)
    ax2.spines["left"].set_color(LGRAY)
    ax2.spines["bottom"].set_color(LGRAY)
    ax2.grid(axis="y", color=LGRAY, linewidth=0.8, zorder=0)

    # ── Legend strip at bottom ─────────────────────────────────
    fig.text(0.5, -0.04,
             "━  Market rate (5/1 ARM)    - - -  Your current rate (5.25%)"
             "    ■ green = rate fell    ■ red = rate rose",
             ha="center", fontsize=7.5, color=MGRAY)

    fig.get_layout_engine().set(w_pad=0.1, h_pad=0.1)
    png_bytes = _to_bytes(fig)
    plt.close(fig)
    return png_bytes
