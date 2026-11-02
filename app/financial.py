from datetime import datetime, timedelta, date
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


def calculate_daily_sales_revenue(start_date=None, end_date=None, branch_id=None):
    """Return actual paid revenue grouped by day for the selected 30-day window."""
    from app import models

    end_date = end_date or date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=29)
    if start_date > end_date:
        start_date = end_date
    period_values = calculate_period_sales_revenue(
        models.Sale.created_at,
        "%Y-%m-%d",
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
        branch_id,
    )
    labels = []
    values = []
    current = start_date
    while current <= end_date:
        key = current.strftime("%Y-%m-%d")
        labels.append(current.strftime("%d.%m"))
        values.append(float(period_values.get(key, ZERO).quantize(Decimal("0.01"))))
        current += timedelta(days=1)
    return {"labels": labels, "values": values}


def calculate_monthly_sales_revenue(start_date=None, end_date=None, branch_id=None):
    """Return actual paid revenue grouped by month for the selected 12-month window."""
    from app import models

    end_date = end_date or date.today()
    month_index = end_date.year * 12 + end_date.month - 1
    if start_date is None:
        start_month_index = month_index - 11
    else:
        start_month_index = start_date.year * 12 + start_date.month - 1
    start_year, zero_based_month = divmod(start_month_index, 12)
    start_date = date(start_year, zero_based_month + 1, 1)
    period_values = calculate_period_sales_revenue(
        models.Sale.created_at,
        "%Y-%m",
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
        branch_id,
    )
    month_names = (
        "январь", "февраль", "март", "апрель", "май", "июнь",
        "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
    )
    labels = []
    values = []
    current_year = start_date.year
    current_month = start_date.month
    while (current_year * 12 + current_month - 1) <= month_index:
        key = f"{current_year:04d}-{current_month:02d}"
        labels.append(f"{month_names[current_month - 1]} {current_year}")
        values.append(float(period_values.get(key, ZERO).quantize(Decimal("0.01"))))
        current_month += 1
        if current_month == 13:
            current_month = 1
            current_year += 1
    return {"labels": labels, "values": values}


def calculate_period_sales_revenue(created_at_column, period_format, start_at, end_at, branch_id=None):
    """Aggregate gross sales less loyalty discounts in SQL, then group by period."""
    from app import db, models

    period = db.func.strftime(period_format, created_at_column).label("period")
    query = db.session.query(
        period,
        db.func.coalesce(db.func.sum(models.Sale.total_amount), 0),
        db.func.coalesce(db.func.sum(models.Sale.discount), 0),
    ).filter(
        models.Sale.created_at >= start_at,
        models.Sale.created_at < end_at,
    )
    if branch_id is not None:
        query = query.filter(models.Sale.branch_id == branch_id)
    result = {}
    for period_key, gross, discount in query.group_by(period).all():
        gross_decimal = Decimal(str(gross))
        discount_decimal = Decimal(str(discount))
        result[period_key] = gross_decimal - discount_decimal
    return result


def calculate_top_products(limit=5, branch_id=None, date_from=None, date_to=None):
    """Return products ranked by units sold from completed sales."""
    from app import db, models

    query = db.session.query(
        models.Product.id.label("product_id"),
        models.Product.name.label("product_name"),
        db.func.coalesce(db.func.sum(models.SaleItem.quantity), 0).label("units_sold"),
        db.func.coalesce(
            db.func.sum(models.SaleItem.quantity * models.SaleItem.unit_price),
            0,
        ).label("gross_sales"),
    ).join(
        models.SaleItem, models.SaleItem.product_id == models.Product.id,
    ).join(
        models.Sale, models.Sale.id == models.SaleItem.sale_id,
    )
    if branch_id is not None:
        query = query.filter(models.Sale.branch_id == branch_id)
    if date_from is not None:
        query = query.filter(models.Sale.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to is not None:
        query = query.filter(models.Sale.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    rows = query.group_by(
        models.Product.id,
        models.Product.name,
    ).order_by(
        db.func.sum(models.SaleItem.quantity).desc(),
        models.Product.name.asc(),
    ).limit(limit).all()

    return [
        {
            "product_id": product_id,
            "product_name": product_name,
            "units_sold": int(units_sold),
            "gross_sales": Decimal(str(gross_sales)).quantize(Decimal("0.01")),
        }
        for product_id, product_name, units_sold, gross_sales in rows
    ]


def calculate_category_sales(branch_id=None, date_from=None, date_to=None):
    """Return gross item sales grouped by category without allocating loyalty discounts."""
    from app import db, models

    query = db.session.query(
        db.func.coalesce(models.Category.name, "Без категории").label("category_name"),
        db.func.coalesce(
            db.func.sum(models.SaleItem.quantity * models.SaleItem.unit_price),
            0,
        ).label("gross_sales"),
    ).select_from(models.SaleItem).join(
        models.Product, models.Product.id == models.SaleItem.product_id,
    ).join(
        models.Sale, models.Sale.id == models.SaleItem.sale_id,
    ).outerjoin(
        models.Category, models.Category.id == models.Product.category_id,
    )
    if branch_id is not None:
        query = query.filter(models.Sale.branch_id == branch_id)
    if date_from is not None:
        query = query.filter(models.Sale.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to is not None:
        query = query.filter(models.Sale.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    category_name_expression = db.func.coalesce(models.Category.name, "Без категории")
    rows = query.group_by(category_name_expression).order_by(category_name_expression.asc()).all()
    return [
        {
            "category_name": category_name,
            "gross_sales": Decimal(str(gross_sales)).quantize(Decimal("0.01")),
        }
        for category_name, gross_sales in rows
    ]


def calculate_low_stock(branch_id=None, limit=None):
    """Return branch inventory at or below its configured minimum stock."""
    from app import db, models

    query = db.session.query(
        models.Product.id,
        models.Product.name,
        models.Branch.id,
        models.Branch.name,
        models.BranchInventory.stock_quantity,
        models.BranchInventory.minimum_stock,
    ).join(
        models.BranchInventory, models.BranchInventory.product_id == models.Product.id,
    ).join(
        models.Branch, models.Branch.id == models.BranchInventory.branch_id,
    ).filter(models.BranchInventory.stock_quantity <= models.BranchInventory.minimum_stock)
    if branch_id is not None:
        query = query.filter(models.BranchInventory.branch_id == branch_id)

    query = query.order_by(
        models.Branch.name.asc(),
        models.Product.name.asc(),
        models.Product.id.asc(),
    )
    if limit is not None:
        query = query.limit(limit)

    return [
        {
            "product_id": product_id,
            "product_name": product_name,
            "branch_id": inventory_branch_id,
            "branch_name": branch_name,
            "stock_quantity": stock_quantity,
            "minimum_stock": minimum_stock,
        }
        for product_id, product_name, inventory_branch_id, branch_name, stock_quantity, minimum_stock in query.all()
    ]
