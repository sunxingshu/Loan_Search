#!/usr/bin/env python3
"""Refi Watch — weekly mortgage refinance intelligence email."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main(dry_run: bool = False, output_file: str | None = None) -> None:
    from src.amortization import get_loan_state
    from src.brokers import get_brokers
    from src.email_composer import compose_email, send_email
    from src.optimizer import (
        build_amount_scenarios,
        build_action_text,
        build_opportunity_spectrum,
        compute_signal,
        draft_outreach_email,
    )
    from src.rates import compute_lender_rates, fetch_rates, load_rate_history, save_rate_history

    config_path = Path("config/loan.json")
    if not config_path.exists():
        print("ERROR: config/loan.json not found. Copy and edit the provided template.", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    # ── 1. Loan state ────────────────────────────────────────────
    loan_state = get_loan_state(config)
    print(f"Loan balance: ${loan_state.balance:,.0f}  |  LTV: {loan_state.ltv:.1f}%  |  ARM in: {loan_state.months_to_adjustment} months")

    # ── 2. Rates ─────────────────────────────────────────────────
    fred_key = os.getenv("FRED_API_KEY", "")
    if not fred_key:
        print("WARNING: FRED_API_KEY not set — rates will be unavailable.", file=sys.stderr)

    current_rates = fetch_rates(fred_key)
    print(f"Rates — 30yr: {current_rates.get('rate_30yr_fixed')}%  |  5/1 ARM: {current_rates.get('rate_5_1_arm')}%")

    history = load_rate_history()
    # Avoid duplicate entries for same date
    if not history or history[-1]["date"] != current_rates["date"]:
        history.append(current_rates)
    history = history[-52:]  # Keep one year
    save_rate_history(history)

    # ── 3. Lender estimates ──────────────────────────────────────
    arm_benchmark = current_rates.get("rate_5_1_arm") or config["loan"]["rate"]
    lender_rates = compute_lender_rates(arm_benchmark, config)
    best_lender = lender_rates[0]

    # ── 4. Opportunity analysis ──────────────────────────────────
    signal = compute_signal(config["loan"]["rate"], best_lender["your_rate"])
    spectrum = build_opportunity_spectrum(loan_state)
    amount_scenarios = build_amount_scenarios(loan_state, config, best_lender["your_rate"])
    action_text = build_action_text(signal, best_lender, best_lender["your_rate"])

    draft = None
    if signal.label == "GREEN":
        draft = draft_outreach_email(config, loan_state, best_lender, best_lender["your_rate"])

    # ── 5. Brokers ───────────────────────────────────────────────
    google_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    brokers_data = get_brokers(config, google_key)
    print(f"Brokers: {len(brokers_data.get('brokers', []))} loaded (source: {brokers_data.get('source')})")

    # ── 6. Compose email ─────────────────────────────────────────
    html = compose_email(
        config=config,
        loan_state=loan_state,
        current_rates=current_rates,
        rate_history=history,
        brokers_data=brokers_data,
        signal=signal,
        spectrum=spectrum,
        lender_rates=lender_rates,
        amount_scenarios=amount_scenarios,
        action_text=action_text,
        draft_email=draft,
    )

    # ── 7. Send or save ──────────────────────────────────────────
    if output_file:
        Path(output_file).write_text(html)
        print(f"Email saved to {output_file}")
        return

    if dry_run:
        out = Path("data/last_email_preview.html")
        out.write_text(html)
        print(f"Dry run — email saved to {out}. Open in browser to preview.")
        return

    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_password:
        print("ERROR: GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env", file=sys.stderr)
        sys.exit(1)

    send_email(html, config, gmail_user, gmail_password)
    print(f"✓ Refi Watch email sent to {config['email']['to']}  |  Signal: {signal.label}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    out = None
    for arg in sys.argv[1:]:
        if arg.startswith("--output="):
            out = arg.split("=", 1)[1]
    main(dry_run=dry, output_file=out)
