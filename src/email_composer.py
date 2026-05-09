"""Email composition and Gmail SMTP delivery.

Chart is embedded as a CID (Content-ID) inline MIME attachment — the
email-standard way to include images. This avoids Gmail's 102 KB body
clipping that breaks base64 data URIs embedded directly in HTML.

For local preview (dry run), the chart is inlined as a base64 data URI
so it renders correctly when opened in a browser.
"""

import base64
import smtplib
from datetime import date, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.amortization import LoanState
from src.optimizer import SignalLevel

CHART_CID = "rate_chart@refiwatch"


def _sparkline(rates: list[float | None]) -> str:
    if not rates:
        return "no data yet"
    formatted = [f"{r:.2f}%" if r is not None else "?" for r in rates]
    if len(formatted) >= 2:
        last, prev = rates[-1], rates[-2]
        if last is not None and prev is not None:
            arrow = (" ↓↓" if last < prev - 0.1 else " ↑↑" if last > prev + 0.1
                     else " ↓" if last < prev else " ↑" if last > prev else " →")
        else:
            arrow = ""
        return "  →  ".join(formatted) + arrow
    return "  →  ".join(formatted)


def _trend_summary(rates: list[float | None]) -> str:
    valid = [r for r in rates if r is not None]
    if len(valid) < 2:
        return "Insufficient history for trend."
    change = valid[-1] - valid[0]
    weeks = len(valid) - 1
    direction = "fallen" if change < 0 else "risen"
    return f"Rates have {direction} {abs(change):.2f}% over the last {weeks} week{'s' if weeks != 1 else ''}."


def _next_friday(from_date: date) -> date:
    days_ahead = 4 - from_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def _render_template(
    config: dict,
    loan_state: LoanState,
    current_rates: dict,
    rate_history: list[dict],
    brokers_data: dict,
    signal: SignalLevel,
    spectrum: list[dict],
    lender_rates: list[dict],
    amount_scenarios: list[dict],
    action_text: str,
    draft_email: dict | None,
    rate_chart_src: str,          # either "cid:..." or "data:image/png;base64,..."
) -> str:
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=False)
    template = env.get_template("email.html")

    today = date.today()
    trend_data = [h.get("rate_5_1_arm") for h in rate_history[-4:]]
    if current_rates.get("rate_5_1_arm"):
        trend_data.append(current_rates["rate_5_1_arm"])
    trend_data = trend_data[-4:]

    best_lender = lender_rates[0]
    borrower = config["borrower"]
    brokers = brokers_data.get("brokers", [])
    last_updated = brokers_data.get("last_updated", "unknown")
    source = brokers_data.get("source", "static_fallback")
    try:
        from datetime import datetime
        lu_date = datetime.strptime(last_updated, "%Y-%m-%d").date()
        next_refresh = (lu_date + timedelta(days=config["brokers"]["refresh_days"])).strftime("%b %d, %Y")
        last_updated_fmt = lu_date.strftime("%b %d, %Y")
    except Exception:
        next_refresh = "—"
        last_updated_fmt = last_updated

    return template.render(
        address=config["property"]["address"],
        report_date=today.strftime("%A, %B %-d, %Y"),
        next_email_date=_next_friday(today).strftime("%B %-d, %Y"),
        signal=signal,
        current_rate=config["loan"]["rate"],
        best_lender=best_lender,
        rate_30yr=current_rates.get("rate_30yr_fixed") or 0,
        rate_5_1_arm=current_rates.get("rate_5_1_arm") or 0,
        sparkline=_sparkline(trend_data),
        trend_summary=_trend_summary(trend_data),
        rate_chart_src=rate_chart_src,
        loan=loan_state,
        spectrum=spectrum,
        lenders=lender_rates,
        relationship_assets=borrower["relationship_assets_current"],
        relationship_assets_max=borrower["relationship_assets_max"],
        amount_scenarios=amount_scenarios,
        combined_tax_rate=borrower["federal_tax_rate"] + borrower["state_tax_rate"],
        brokers=brokers,
        brokers_last_updated=last_updated_fmt,
        brokers_source="Google Places" if source == "google_places" else "Curated list",
        brokers_next_refresh=next_refresh,
        action_text=action_text,
        draft_email=draft_email,
    )


def compose_email(
    config: dict,
    loan_state: LoanState,
    current_rates: dict,
    rate_history: list[dict],
    brokers_data: dict,
    signal: SignalLevel,
    spectrum: list[dict],
    lender_rates: list[dict],
    amount_scenarios: list[dict],
    action_text: str,
    draft_email: dict | None,
    preview: bool = False,
) -> tuple[str, bytes | None]:
    """Return (html_string, chart_png_bytes).

    preview=True  → chart embedded as base64 data URI (for browser viewing)
    preview=False → chart referenced as cid: (for email sending)
    """
    from src.charts import generate_chart
    chart_bytes = generate_chart(rate_history, config["loan"]["rate"])

    if chart_bytes and preview:
        chart_src = "data:image/png;base64," + base64.b64encode(chart_bytes).decode()
    elif chart_bytes:
        chart_src = f"cid:{CHART_CID}"
    else:
        chart_src = ""

    html = _render_template(
        config, loan_state, current_rates, rate_history, brokers_data,
        signal, spectrum, lender_rates, amount_scenarios, action_text,
        draft_email, chart_src,
    )
    return html, chart_bytes


def _recipients(config: dict) -> list[str]:
    to = config["email"]["to"]
    return to if isinstance(to, list) else [to]


def send_email(
    html: str,
    chart_bytes: bytes | None,
    config: dict,
    gmail_user: str,
    gmail_password: str,
) -> None:
    today = date.today()
    loan = config["loan"]
    subject = f"{config['email']['subject_prefix']} — {today.strftime('%b %d, %Y')} | {loan['rate']}% ARM"
    recipients = _recipients(config)

    # multipart/related wraps HTML + inline image together
    msg_root = MIMEMultipart("related")
    msg_root["Subject"] = subject
    msg_root["From"] = gmail_user
    msg_root["To"] = ", ".join(recipients)

    # multipart/alternative inside so email clients can pick the best format
    msg_alt = MIMEMultipart("alternative")
    msg_root.attach(msg_alt)
    msg_alt.attach(MIMEText(html, "html"))

    # Attach chart as inline CID image
    if chart_bytes:
        img = MIMEImage(chart_bytes, "png")
        img.add_header("Content-ID", f"<{CHART_CID}>")
        img.add_header("Content-Disposition", "inline", filename="rate_chart.png")
        msg_root.attach(img)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, recipients, msg_root.as_string())
