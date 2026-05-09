"""Email composition and Gmail SMTP delivery."""

import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.amortization import LoanState
from src.optimizer import SignalLevel


def _sparkline(rates: list[float | None]) -> str:
    if not rates:
        return "no data yet"
    formatted = [f"{r:.2f}%" if r is not None else "?" for r in rates]
    if len(formatted) >= 2:
        last = rates[-1]
        prev = rates[-2]
        if last is not None and prev is not None:
            arrow = " ↓↓" if last < prev - 0.1 else " ↑↑" if last > prev + 0.1 else " ↓" if last < prev else " ↑" if last > prev else " →"
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
    return f"Rates have {direction} {abs(change):.2f}% over the last {weeks} week{'s' if weeks > 1 else ''}."


def _next_friday(from_date: date) -> date:
    days_ahead = 4 - from_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


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
) -> str:
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=False)
    template = env.get_template("email.html")

    today = date.today()
    trend_data = [h.get("rate_5_1_arm") for h in rate_history[-4:]]

    # Add current week's rate if not already in history
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

    # Generate rate chart (gracefully skipped if matplotlib unavailable)
    from src.charts import generate_chart
    chart_b64 = generate_chart(rate_history, config["loan"]["rate"])

    html = template.render(
        # Header
        address=config["property"]["address"],
        report_date=today.strftime("%A, %B %-d, %Y"),
        next_email_date=_next_friday(today).strftime("%B %-d, %Y"),
        # Signal
        signal=signal,
        current_rate=config["loan"]["rate"],
        best_lender=best_lender,
        # Market
        rate_30yr=current_rates.get("rate_30yr_fixed") or 0,
        rate_5_1_arm=current_rates.get("rate_5_1_arm") or 0,
        sparkline=_sparkline(trend_data),
        trend_summary=_trend_summary(trend_data),
        rate_chart_b64=chart_b64,
        # Loan
        loan=loan_state,
        # Opportunity
        spectrum=spectrum,
        # Lenders
        lenders=lender_rates,
        relationship_assets=borrower["relationship_assets_current"],
        relationship_assets_max=borrower["relationship_assets_max"],
        # Amount optimizer
        amount_scenarios=amount_scenarios,
        combined_tax_rate=borrower["federal_tax_rate"] + borrower["state_tax_rate"],
        # Brokers
        brokers=brokers,
        brokers_last_updated=last_updated_fmt,
        brokers_source="Google Places" if source == "google_places" else "Curated list",
        brokers_next_refresh=next_refresh,
        # Action
        action_text=action_text,
        draft_email=draft_email,
    )
    return html


def send_email(html: str, config: dict, gmail_user: str, gmail_password: str) -> None:
    today = date.today()
    loan = config["loan"]
    best_rate_placeholder = config["loan"]["rate"]

    subject = f"{config['email']['subject_prefix']} — {today.strftime('%b %d, %Y')} | {loan['rate']}% ARM"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = config["email"]["to"]
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, config["email"]["to"], msg.as_string())
