"""Refinance opportunity analysis: savings spectrum, amount optimizer, tax math."""

from dataclasses import dataclass

from src.amortization import LoanState, _monthly_payment


@dataclass
class SignalLevel:
    label: str        # GREEN / YELLOW / RED
    emoji: str
    headline: str
    subtext: str
    bg_color: str
    text_color: str
    subtext_color: str


def _lifetime_interest(principal: float, annual_rate_pct: float, term_months: int) -> float:
    payment = _monthly_payment(principal, annual_rate_pct, term_months)
    return payment * term_months - principal


def _tax_deductibility(loan_amount: float, deduction_limit: float) -> float:
    """Return fraction of interest that is tax-deductible (0.0–1.0)."""
    if loan_amount <= deduction_limit:
        return 1.0
    return deduction_limit / loan_amount


def savings_at_rate(
    loan_state: LoanState,
    target_rate_pct: float,
    refi_amount: float,
    term_months: int = 360,
) -> dict:
    new_payment = _monthly_payment(refi_amount, target_rate_pct, term_months)
    # Remaining term on current loan
    payments_remaining = 360 - round(
        (loan_state.monthly_payment * 360 - loan_state.balance * (360 / 360)) / loan_state.monthly_payment, 0
    )
    monthly_savings = loan_state.monthly_payment - new_payment
    current_remaining_interest = loan_state.monthly_payment * term_months - refi_amount
    new_total_interest = new_payment * term_months - refi_amount
    lifetime_savings = current_remaining_interest - new_total_interest
    return {
        "target_rate": target_rate_pct,
        "refi_amount": refi_amount,
        "new_payment": round(new_payment, 2),
        "monthly_savings": round(monthly_savings, 2),
        "lifetime_savings": round(lifetime_savings, 2),
    }


def build_opportunity_spectrum(loan_state: LoanState) -> list[dict]:
    """Show savings table at 5%, 4.75%, 4.5%, 4.25%, 4.0%."""
    rows = []
    for rate in [5.00, 4.75, 4.50, 4.25, 4.00]:
        s = savings_at_rate(loan_state, rate, loan_state.balance)
        rows.append(s)
    return rows


def build_amount_scenarios(
    loan_state: LoanState, config: dict, best_rate: float
) -> list[dict]:
    """Model refinance at current balance, $840k, $800k, $750k."""
    borrower = config["borrower"]
    deduction_limit = borrower["mortgage_deduction_limit"]
    combined_tax_rate = borrower["federal_tax_rate"] + borrower["state_tax_rate"]
    annual_interest_current = loan_state.monthly_payment * 12 - (loan_state.balance * 0.01)
    home_value = config["property"]["estimated_value"]

    scenarios = []
    amounts = [loan_state.balance, 840_000, 800_000, 750_000]
    for amount in amounts:
        if amount > loan_state.balance + 1:
            continue
        cash_needed = max(0.0, loan_state.balance - amount)
        ltv = amount / home_value * 100
        deductibility = _tax_deductibility(amount, deduction_limit)
        new_payment = _monthly_payment(amount, best_rate, 360)
        monthly_savings = loan_state.monthly_payment - new_payment

        # Annual tax savings from full vs partial deductibility
        annual_interest_new = new_payment * 12 - (amount / 30)
        non_deductible = annual_interest_new * (1 - deductibility)
        annual_tax_cost_saved = non_deductible * combined_tax_rate

        # Opportunity cost of cash brought to close (at 7% invested)
        opportunity_cost_monthly = cash_needed * 0.07 / 12

        is_sweet_spot = abs(amount - deduction_limit) < 1000

        scenarios.append({
            "amount": round(amount, 0),
            "cash_needed": round(cash_needed, 0),
            "ltv": round(ltv, 1),
            "deductibility_pct": round(deductibility * 100, 1),
            "new_payment": round(new_payment, 2),
            "monthly_savings": round(monthly_savings, 2),
            "annual_tax_cost_saved": round(annual_tax_cost_saved, 0),
            "opportunity_cost_monthly": round(opportunity_cost_monthly, 0),
            "is_sweet_spot": is_sweet_spot,
            "is_current": cash_needed == 0,
        })
    return scenarios


def compute_signal(current_rate: float, best_available: float) -> SignalLevel:
    diff = current_rate - best_available
    if diff >= 0.50:
        return SignalLevel(
            label="GREEN",
            emoji="🟢",
            headline="STRONG OPPORTUNITY",
            subtext=f"Best rate for your profile is {diff:.2f}% below your current rate.",
            bg_color="#dcfce7",
            text_color="#14532d",
            subtext_color="#166534",
        )
    elif diff >= 0.25:
        return SignalLevel(
            label="YELLOW",
            emoji="🟡",
            headline="GETTING INTERESTING",
            subtext=f"Best rate for your profile is {diff:.2f}% below your current rate. Getting close.",
            bg_color="#fef9c3",
            text_color="#713f12",
            subtext_color="#854d0e",
        )
    elif diff > 0:
        return SignalLevel(
            label="YELLOW",
            emoji="🟡",
            headline="WATCH CLOSELY",
            subtext=f"Only {diff:.2f}% below your current rate. Not worth moving yet.",
            bg_color="#fef9c3",
            text_color="#713f12",
            subtext_color="#854d0e",
        )
    else:
        return SignalLevel(
            label="RED",
            emoji="🔴",
            headline="HOLD — MARKET ABOVE YOUR RATE",
            subtext=f"Market is {abs(diff):.2f}% ABOVE your current rate. Your 5/1 ARM is below market.",
            bg_color="#fee2e2",
            text_color="#7f1d1d",
            subtext_color="#991b1b",
        )


def build_action_text(signal: SignalLevel, best_lender: dict, best_rate: float) -> str:
    if signal.label == "GREEN":
        return (
            f"Rates have moved meaningfully in your favor. "
            f"{best_lender['name']} offers an estimated {best_rate:.3f}% with your relationship discount. "
            f"Worth reaching out this week — draft email below."
        )
    elif signal.label == "YELLOW":
        return (
            f"You're getting closer to a refinance opportunity. "
            f"Best estimate is {best_rate:.3f}% at {best_lender['name']}. "
            f"Monitor weekly — no action needed yet."
        )
    else:
        return (
            f"Market rates are above your current {best_lender.get('current_rate', 5.25):.2f}% ARM. "
            f"Your rate is competitive. Sit tight and keep watching."
        )


def draft_outreach_email(config: dict, loan_state: LoanState, best_lender: dict, best_rate: float) -> dict:
    loan = config["loan"]
    prop = config["property"]
    borrower = config["borrower"]
    assets = borrower["relationship_assets_current"]

    subject = (
        f"Refinance Inquiry — Loan #{loan['loan_number']}, "
        f"{loan['rate']}% {loan['arm_type']} ARM, {prop['address'].split(',')[1].strip()}"
    )

    body = f"""Hi [Name],

I'd like to explore refinancing options for my primary residence at {prop['address']}.

Quick snapshot so you can tell me quickly if it's worth a conversation:

  • Current loan:   ${loan_state.balance:,.0f} balance, {loan['rate']}% {loan['arm_type']} ARM (closed Dec 2025)
  • Home value:     ~${prop['estimated_value']:,.0f}  (LTV {loan_state.ltv:.1f}%)
  • Credit:         Excellent — dual borrowers, low DTI
  • Assets at {best_lender['name']}: ${assets:,.0f} (can increase to ${borrower['relationship_assets_max']:,.0f})
  • Preference:     No-cost refinance if possible

I prefer to start by email. If the rate you can offer for my profile looks interesting,
I'm happy to connect by phone or Zoom.

Would love to know what rate range you'd estimate today, and which products you'd recommend.

Thank you,
Xingshu"""

    return {"subject": subject, "body": body}
