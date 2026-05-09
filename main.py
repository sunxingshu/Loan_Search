#!/usr/bin/env python3
"""Refi Watch — weekly mortgage refinance intelligence email."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def log(msg: str) -> None:
    print(msg, flush=True)


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

    log("=" * 60)
    log("REFI WATCH — starting run")
    log("=" * 60)

    # ── Config ───────────────────────────────────────────────────
    config_path = Path("config/loan.json")
    if not config_path.exists():
        log("ERROR: config/loan.json not found.")
        sys.exit(1)
    with open(config_path) as f:
        config = json.load(f)
    log(f"[config] loaded  →  {config['property']['address']}")
    log(f"[config] send to →  {config['email']['to']}")

    # ── 1. Loan state ────────────────────────────────────────────
    log("\n[1/7] Calculating loan state...")
    loan_state = get_loan_state(config)
    log(f"      balance:          ${loan_state.balance:,.0f}")
    log(f"      LTV:              {loan_state.ltv:.2f}%")
    log(f"      monthly payment:  ${loan_state.monthly_payment:,.2f}")
    log(f"      ARM adjustment:   {loan_state.adjustment_date}  ({loan_state.months_to_adjustment} months away)")
    log(f"      worst-case pmt:   ${loan_state.worst_case_payment:,.0f} at {loan_state.worst_case_rate}%")

    # ── 2. Rates ─────────────────────────────────────────────────
    log("\n[2/7] Fetching mortgage rates from FRED...")
    fred_key = os.getenv("FRED_API_KEY", "")
    log(f"      FRED_API_KEY set: {'YES (' + fred_key[:6] + '...)' if fred_key else 'NO — rates will use fallback'}")

    current_rates = fetch_rates(fred_key)
    log(f"      30yr fixed:  {current_rates.get('rate_30yr_fixed')}%")
    log(f"      5/1 ARM:     {current_rates.get('rate_5_1_arm')}%  (source: {current_rates.get('arm_source')})")

    history = load_rate_history()
    log(f"      history entries so far: {len(history)}")
    if not history or history[-1]["date"] != current_rates["date"]:
        history.append(current_rates)
        log(f"      appended today's rates to history")
    else:
        log(f"      today's rates already in history — skipping duplicate")
    history = history[-52:]
    save_rate_history(history)
    log(f"      history saved  ({len(history)} weeks stored)")

    # ── 3. Lender estimates ──────────────────────────────────────
    log("\n[3/7] Computing lender rate estimates...")
    arm_benchmark = current_rates.get("rate_5_1_arm") or config["loan"]["rate"]
    log(f"      benchmark used: {arm_benchmark}%")
    lender_rates = compute_lender_rates(arm_benchmark, config)
    for lr in lender_rates:
        log(f"      {lr['name']:<15} base={lr['base_rate']}%  discount=-{lr['relationship_discount']}%  your_rate={lr['your_rate']}%")
    best_lender = lender_rates[0]
    log(f"      best lender: {best_lender['name']} at {best_lender['your_rate']}%")

    # ── 4. Opportunity analysis ──────────────────────────────────
    log("\n[4/7] Analysing refinance opportunity...")
    signal = compute_signal(config["loan"]["rate"], best_lender["your_rate"])
    log(f"      signal: {signal.label}  ({signal.headline})")
    spectrum = build_opportunity_spectrum(loan_state)
    log(f"      spectrum rows: {len(spectrum)}")
    amount_scenarios = build_amount_scenarios(loan_state, config, best_lender["your_rate"])
    log(f"      amount scenarios: {len(amount_scenarios)}")
    action_text = build_action_text(signal, best_lender, best_lender["your_rate"])

    draft = None
    if signal.label == "GREEN":
        draft = draft_outreach_email(config, loan_state, best_lender, best_lender["your_rate"])
        log("      draft outreach email generated (GREEN signal)")
    else:
        log("      no draft email (signal is not GREEN)")

    # ── 5. Brokers ───────────────────────────────────────────────
    log("\n[5/7] Loading broker list...")
    google_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    hunter_key = os.getenv("HUNTER_API_KEY", "")
    log(f"      GOOGLE_PLACES_API_KEY set: {'YES (' + google_key[:8] + '...)' if google_key else 'NO — using static list'}")
    log(f"      HUNTER_API_KEY set:        {'YES (' + hunter_key[:6] + '...)' if hunter_key else 'NO — emails from static list'}")

    brokers_data = get_brokers(config, google_key, hunter_key)
    log(f"      source:       {brokers_data.get('source')}")
    log(f"      last updated: {brokers_data.get('last_updated')}")
    for b in brokers_data.get("brokers", []):
        log(f"      {b['name']:<30} ★{b['rating']}  phone={b.get('phone') or '—'}  email={b.get('email') or '—'}")

    # ── 6. Compose email ─────────────────────────────────────────
    log("\n[6/7] Composing email...")
    is_preview = dry_run or bool(output_file)
    html, chart_bytes = compose_email(
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
        preview=is_preview,
    )
    chart_kb = len(chart_bytes) // 1024 if chart_bytes else 0
    log(f"      HTML size:  {len(html):,} chars")
    log(f"      Chart PNG:  {chart_kb} KB  ({'CID attachment' if not is_preview else 'base64 preview'})")

    # ── 7. Send or save ──────────────────────────────────────────
    log("\n[7/7] Sending...")

    if output_file:
        Path(output_file).write_text(html)
        log(f"      saved to {output_file}")
        return

    if dry_run:
        out = Path("data/last_email_preview.html")
        out.write_text(html)
        log(f"      dry run — saved to {out}")
        return

    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
    recipients = config["email"]["to"]
    recipients_display = recipients if isinstance(recipients, list) else [recipients]
    log(f"      GMAIL_USER set:         {'YES (' + gmail_user + ')' if gmail_user else 'NO'}")
    log(f"      GMAIL_APP_PASSWORD set: {'YES' if gmail_password else 'NO'}")
    log(f"      Recipients:             {', '.join(recipients_display)}")

    if not gmail_user or not gmail_password:
        log("ERROR: GMAIL_USER and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)

    log(f"      connecting to smtp.gmail.com:465...")
    send_email(html, chart_bytes, config, gmail_user, gmail_password)

    log("")
    log("=" * 60)
    log(f"✓ EMAIL SENT to {config['email']['to']}")
    log(f"  Signal: {signal.label}  |  Best rate: {best_lender['your_rate']}% ({best_lender['name']})")
    log("=" * 60)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    out = None
    for arg in sys.argv[1:]:
        if arg.startswith("--output="):
            out = arg.split("=", 1)[1]
    main(dry_run=dry, output_file=out)
