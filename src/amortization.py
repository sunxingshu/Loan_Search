"""Loan amortization calculations — balance, payment, LTV, ARM countdown."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class LoanState:
    balance: float
    monthly_payment: float
    ltv: float
    equity: float
    principal_paid: float
    months_to_adjustment: int
    adjustment_date: date
    worst_case_payment: float
    worst_case_rate: float
    months_to_ltv_65: int | None
    months_to_ltv_60: int | None


def _monthly_payment(principal: float, annual_rate_pct: float, term_months: int) -> float:
    r = annual_rate_pct / 100 / 12
    return principal * r * (1 + r) ** term_months / ((1 + r) ** term_months - 1)


def _balance_after_n_payments(
    principal: float, annual_rate_pct: float, term_months: int, n: int
) -> float:
    if n <= 0:
        return principal
    r = annual_rate_pct / 100 / 12
    payment = _monthly_payment(principal, annual_rate_pct, term_months)
    balance = principal * (1 + r) ** n - payment * ((1 + r) ** n - 1) / r
    return max(0.0, balance)


def _months_between(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _months_until_ltv(
    current_balance: float,
    monthly_payment: float,
    annual_rate_pct: float,
    home_value: float,
    target_ltv_pct: float,
) -> int | None:
    target_balance = home_value * target_ltv_pct / 100
    if current_balance <= target_balance:
        return 0
    r = annual_rate_pct / 100 / 12
    balance = current_balance
    for month in range(1, 361):
        interest = balance * r
        principal = monthly_payment - interest
        if principal <= 0:
            return None
        balance -= principal
        if balance <= target_balance:
            return month
    return None


def get_loan_state(config: dict, as_of: date | None = None) -> LoanState:
    if as_of is None:
        as_of = date.today()

    loan = config["loan"]
    prop = config["property"]

    principal = loan["original_amount"]
    rate = loan["rate"]
    term_months = loan["term_years"] * 12
    first_payment = datetime.strptime(loan["first_payment_date"], "%Y-%m-%d").date()
    adjustment_date = datetime.strptime(loan["arm_adjustment_date"], "%Y-%m-%d").date()
    home_value = prop["estimated_value"]
    caps = loan["rate_caps"]

    payments_made = _months_between(first_payment, as_of)
    balance = _balance_after_n_payments(principal, rate, term_months, payments_made)
    payment = _monthly_payment(principal, rate, term_months)
    ltv = balance / home_value * 100
    equity = home_value - balance

    # Worst-case rate: current + lifetime cap
    worst_rate = rate + caps["lifetime"]
    months_to_adj = _months_between(as_of, adjustment_date)
    remaining_months_at_adj = term_months - _months_between(first_payment, adjustment_date)
    balance_at_adj = _balance_after_n_payments(principal, rate, term_months, _months_between(first_payment, adjustment_date))
    worst_case_payment = _monthly_payment(balance_at_adj, worst_rate, max(1, remaining_months_at_adj))

    months_to_65 = _months_until_ltv(balance, payment, rate, home_value, 65.0)
    months_to_60 = _months_until_ltv(balance, payment, rate, home_value, 60.0)

    return LoanState(
        balance=round(balance, 2),
        monthly_payment=round(payment, 2),
        ltv=round(ltv, 2),
        equity=round(equity, 2),
        principal_paid=round(principal - balance, 2),
        months_to_adjustment=months_to_adj,
        adjustment_date=adjustment_date,
        worst_case_payment=round(worst_case_payment, 2),
        worst_case_rate=worst_rate,
        months_to_ltv_65=months_to_65,
        months_to_ltv_60=months_to_60,
    )
