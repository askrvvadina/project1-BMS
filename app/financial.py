from datetime import datetime, timedelta
from decimal import Decimal

ZERO = Decimal("0.00")


def calculate_financial_summary(date_from=None, date_to=None, branch_id=None):
    """Calculate reusable operational totals for the selected period and branch."""
    from app import models

    sales_query = models.Sale.query
    if date_from is not None:
        sales_query = sales_query.filter(
            models.Sale.created_at >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to is not None:
        sales_query = sales_query.filter(
            models.Sale.created_at < datetime.combine(
                date_to + timedelta(days=1), datetime.min.time()
            )
        )
    if branch_id is not None:
        sales_query = sales_query.filter(models.Sale.branch_id == branch_id)

    gross_sales = ZERO
    loyalty_discounts = ZERO
    for total_amount, discount in sales_query.with_entities(
        models.Sale.total_amount,
        models.Sale.discount,
    ):
        gross_sales += Decimal(str(total_amount if total_amount is not None else ZERO))
        loyalty_discounts += Decimal(str(discount if discount is not None else ZERO))

    expenses_query = models.Expense.query
    if date_from is not None:
        expenses_query = expenses_query.filter(models.Expense.date >= date_from)
    if date_to is not None:
        expenses_query = expenses_query.filter(models.Expense.date <= date_to)
    if branch_id is not None:
        expenses_query = expenses_query.filter(models.Expense.branch_id == branch_id)

    total_expenses = sum(
        (Decimal(amount) for (amount,) in expenses_query.with_entities(models.Expense.amount)),
        ZERO,
    )
    actual_paid_sales = gross_sales - loyalty_discounts

    return {
        "gross_sales": gross_sales.quantize(Decimal("0.01")),
        "loyalty_discounts": loyalty_discounts.quantize(Decimal("0.01")),
        "actual_paid_sales": actual_paid_sales.quantize(Decimal("0.01")),
        "total_expenses": total_expenses.quantize(Decimal("0.01")),
        "operational_difference": (actual_paid_sales - total_expenses).quantize(Decimal("0.01")),
    }
