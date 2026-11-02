from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Branch, BranchSalesTarget, User

DEFAULT_DAILY_TARGET = Decimal("15000.00")
DEFAULT_MONTHLY_TARGET = Decimal("450000.00")
MAX_TARGET = Decimal("9999999999.99")


def validate_sales_target(value, field_name="Sales target"):
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a valid non-negative decimal amount.")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"{field_name} must be a valid decimal amount.") from error

    if not amount.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    if amount < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    if amount > MAX_TARGET:
        raise ValueError(f"{field_name} exceeds Numeric(12, 2) precision.")

    try:
        quantized = amount.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError(f"{field_name} must have at most two decimal places.") from error
    if quantized != amount:
        raise ValueError(f"{field_name} must have at most two decimal places.")
    return quantized


def get_branch_sales_targets(branch_id, create_defaults=False):
    branch = db.session.get(Branch, branch_id)
    if branch is None:
        raise ValueError("Branch does not exist.")

    targets = BranchSalesTarget.query.filter_by(branch_id=branch.id).one_or_none()
    if targets is None and create_defaults:
        targets = BranchSalesTarget(
            branch_id=branch.id,
            daily_target=DEFAULT_DAILY_TARGET,
            monthly_target=DEFAULT_MONTHLY_TARGET,
        )
        db.session.add(targets)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            targets = BranchSalesTarget.query.filter_by(branch_id=branch.id).one()
    return targets


def calculate_target_progress(target, actual_revenue):
    """Calculate achievement percentage and remaining amount for a target."""
    target = Decimal(str(target if target is not None else 0)).quantize(Decimal("0.01"))
    actual_revenue = Decimal(str(actual_revenue if actual_revenue is not None else 0)).quantize(Decimal("0.01"))
    if target <= 0:
        return {
            "achievement_percentage": None,
            "remaining_amount": target,
            "is_unset": True,
        }
    achievement = ((actual_revenue / target) * Decimal("100")).quantize(Decimal("0.01"))
    return {
        "achievement_percentage": achievement,
        "remaining_amount": max(target - actual_revenue, Decimal("0.00")),
        "is_unset": False,
    }


def calculate_branch_target_progress(branch_ids, current_date=None):
    """Calculate current-day and current-month progress for an authorized scope."""
    from datetime import date
    from app.financial import calculate_branch_performance

    current_date = current_date or date.today()
    month_start = current_date.replace(day=1)
    if branch_ids is None or not branch_ids:
        return []

    daily = {
        row["branch_id"]: row["actual_paid_revenue"]
        for row in calculate_branch_performance(
            date_from=current_date,
            date_to=current_date,
            authorized_branch_ids=branch_ids,
        )
    }
    monthly = {
        row["branch_id"]: row["actual_paid_revenue"]
        for row in calculate_branch_performance(
            date_from=month_start,
            date_to=current_date,
            authorized_branch_ids=branch_ids,
        )
    }

    target_rows = BranchSalesTarget.query.filter(
        BranchSalesTarget.branch_id.in_(branch_ids)
    ).all()
    targets_by_branch_id = {targets.branch_id: targets for targets in target_rows}

    results = []
    for branch in Branch.query.filter(Branch.id.in_(branch_ids)).order_by(Branch.name.asc()).all():
        targets = targets_by_branch_id.get(branch.id)
        if targets is None:
            daily_target = DEFAULT_DAILY_TARGET
            monthly_target = DEFAULT_MONTHLY_TARGET
        else:
            daily_target = targets.daily_target
            monthly_target = targets.monthly_target
        daily_progress = calculate_target_progress(daily_target, daily.get(branch.id, Decimal("0.00")))
        monthly_progress = calculate_target_progress(monthly_target, monthly.get(branch.id, Decimal("0.00")))
        results.append({
            "branch_id": branch.id,
            "branch_name": branch.name,
            "daily_target": daily_target,
            "daily_revenue": daily.get(branch.id, Decimal("0.00")),
            "daily_achievement_percentage": daily_progress["achievement_percentage"],
            "daily_remaining_amount": daily_progress["remaining_amount"],
            "daily_is_unset": daily_progress["is_unset"],
            "monthly_target": monthly_target,
            "monthly_revenue": monthly.get(branch.id, Decimal("0.00")),
            "monthly_achievement_percentage": monthly_progress["achievement_percentage"],
            "monthly_remaining_amount": monthly_progress["remaining_amount"],
            "monthly_is_unset": monthly_progress["is_unset"],
        })
    return results


def update_branch_sales_targets(branch_id, daily_target, monthly_target, actor):
    if not isinstance(actor, User) or (actor.role or "").lower() != "admin":
        raise PermissionError("Only administrators can modify branch sales targets.")

    branch = db.session.get(Branch, branch_id)
    if branch is None:
        raise ValueError("Branch does not exist.")

    daily_target = validate_sales_target(daily_target, "Daily sales target")
    monthly_target = validate_sales_target(monthly_target, "Monthly sales target")
    targets = get_branch_sales_targets(branch.id, create_defaults=True)
    targets.daily_target = daily_target
    targets.monthly_target = monthly_target
    db.session.commit()
    return targets
