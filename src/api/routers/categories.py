"""Category endpoints."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import CurrentUser, get_conn, get_current_user, require_admin, scope_condition, validate_scope
from src.api.models import (
    CategoryCreate,
    CategoryDelete,
    CategoryItem,
    CategoryRename,
    CategoryTree,
    SpendingByCategory,
    SpendingReport,
)

router = APIRouter()


@router.get("/categories", response_model=CategoryTree)
def list_categories(
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Get the full category tree."""
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, full_path, category_type, is_active, parent_id
        FROM category
        ORDER BY full_path
    """)

    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    # Build flat list and index by id
    all_cats: dict[UUID, dict] = {}
    for row in rows:
        cat = dict(zip(columns, row))
        cat["children"] = []
        all_cats[cat["id"]] = cat

    # Build tree
    roots = []
    for cat in all_cats.values():
        parent_id = cat["parent_id"]
        if parent_id and parent_id in all_cats:
            all_cats[parent_id]["children"].append(cat)
        else:
            roots.append(cat)

    def _to_model(cat_dict: dict) -> CategoryItem:
        return CategoryItem(
            id=cat_dict["id"],
            name=cat_dict["name"],
            full_path=cat_dict["full_path"],
            category_type=cat_dict["category_type"],
            is_active=cat_dict["is_active"],
            parent_id=cat_dict["parent_id"],
            children=[_to_model(c) for c in cat_dict["children"]],
        )

    items = [_to_model(r) for r in roots]
    return CategoryTree(items=items)


@router.get("/categories/spending", response_model=SpendingReport)
def spending_by_category(
    date_from: date = Query(..., description="Start date (inclusive)"),
    date_to: date = Query(..., description="End date (inclusive)"),
    institution: str | None = None,
    account_ref: str | None = None,
    currency: str = Query("GBP", description="Currency to report in"),
    scope: str | None = Query("personal", description="Scope filter (personal/business/all)"),
    conn=Depends(get_conn),
    user: CurrentUser = Depends(get_current_user),
):
    """Get spending aggregated by category for a date range."""
    cur = conn.cursor()

    effective_scope = validate_scope(scope, user)
    scope_cond, scope_params = scope_condition(effective_scope, user, alias="acct")

    conditions = [
        "rt.posted_at >= %(date_from)s",
        "rt.posted_at <= %(date_to)s",
        "rt.currency = %(currency)s",
        "(acct.exclude_from_reports IS NOT TRUE)",
        scope_cond,
    ]
    params: dict = {
        "date_from": date_from,
        "date_to": date_to,
        "currency": currency,
        **scope_params,
    }

    if institution:
        conditions.append("rt.institution = %(institution)s")
        params["institution"] = institution
    if account_ref:
        conditions.append("rt.account_ref = %(account_ref)s")
        params["account_ref"] = account_ref

    where = " AND ".join(conditions)

    cur.execute(f"""
        WITH effective_lines AS (
            -- Unsplit transactions: normal category resolution
            SELECT rt.amount, rt.currency, rt.posted_at,
                   rt.institution, rt.account_ref,
                   COALESCE(tcat.full_path, cat_override.full_path, cat.full_path) AS category_path,
                   COALESCE(tcat.name, cat_override.name, cat.name) AS category_name,
                   COALESCE(tcat.category_type, cat_override.category_type, cat.category_type) AS category_type
            FROM active_transaction rt
            LEFT JOIN account acct
                ON acct.institution = rt.institution AND acct.account_ref = rt.account_ref
            LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
            LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
            LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
            LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
            LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
            LEFT JOIN category cat ON cat.full_path = cm.category_hint
            LEFT JOIN category cat_override ON cat_override.full_path = cm_override.category_hint
            LEFT JOIN transaction_category_override tco ON tco.raw_transaction_id = rt.id
            LEFT JOIN category tcat ON tcat.full_path = tco.category_path
            WHERE NOT EXISTS (
                SELECT 1 FROM transaction_split_line sl WHERE sl.raw_transaction_id = rt.id
            )
            AND {where}

            UNION ALL

            -- Split transactions: each line has its own amount + category
            SELECT sl.amount, sl.currency, rt.posted_at,
                   rt.institution, rt.account_ref,
                   sl.category_path,
                   scat.name AS category_name,
                   scat.category_type
            FROM active_transaction rt
            LEFT JOIN account acct
                ON acct.institution = rt.institution AND acct.account_ref = rt.account_ref
            JOIN transaction_split_line sl ON sl.raw_transaction_id = rt.id
            LEFT JOIN category scat ON scat.full_path = sl.category_path
            WHERE {where}
        )
        SELECT
            COALESCE(el.category_path, 'Uncategorised') AS category_path,
            COALESCE(el.category_name, 'Uncategorised') AS category_name,
            el.category_type,
            SUM(el.amount) AS total,
            COUNT(*) AS transaction_count
        FROM effective_lines el
        WHERE COALESCE(el.category_path, 'Uncategorised') NOT LIKE '+%%'
        GROUP BY COALESCE(el.category_path, 'Uncategorised'),
                 COALESCE(el.category_name, 'Uncategorised'),
                 el.category_type
        ORDER BY total ASC
    """, params)

    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    items = [SpendingByCategory(**dict(zip(columns, row))) for row in rows]

    total_income = sum(i.total for i in items if i.total > 0)
    total_expense = sum(i.total for i in items if i.total < 0)

    return SpendingReport(
        items=items,
        date_from=date_from,
        date_to=date_to,
        total_income=total_income,
        total_expense=total_expense,
    )


# ── Category Management ──────────────────────────────────────────────────────


@router.post("/categories")
def create_category(body: CategoryCreate, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Create a new category."""
    if body.category_type not in ("income", "expense"):
        raise HTTPException(400, "category_type must be 'income' or 'expense'")

    cur = conn.cursor()

    # Compute full_path from parent
    if body.parent_id:
        cur.execute("SELECT full_path FROM category WHERE id = %s", (str(body.parent_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Parent category not found")
        full_path = f"{row[0]}:{body.name}"
    else:
        full_path = body.name

    # Check uniqueness
    cur.execute("SELECT id FROM category WHERE full_path = %s", (full_path,))
    if cur.fetchone():
        raise HTTPException(409, f"Category '{full_path}' already exists")

    cur.execute("""
        INSERT INTO category (name, full_path, parent_id, category_type)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """, (body.name, full_path, str(body.parent_id) if body.parent_id else None, body.category_type))
    cat_id = cur.fetchone()[0]
    conn.commit()

    return {"id": str(cat_id), "full_path": full_path, "created": True}


@router.put("/categories/{category_id}/rename")
def rename_category(category_id: UUID, body: CategoryRename, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Rename a category, cascading path changes to children and references."""
    cur = conn.cursor()

    cur.execute("SELECT full_path, name, parent_id FROM category WHERE id = %s", (str(category_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Category not found")
    old_path, old_name, parent_id = row

    # Compute new path
    if parent_id:
        cur.execute("SELECT full_path FROM category WHERE id = %s", (str(parent_id),))
        parent_path = cur.fetchone()[0]
        new_path = f"{parent_path}:{body.new_name}"
    else:
        new_path = body.new_name

    if new_path == old_path:
        return {"id": str(category_id), "full_path": new_path, "renamed": False}

    # Check uniqueness
    cur.execute("SELECT id FROM category WHERE full_path = %s AND id != %s", (new_path, str(category_id)))
    if cur.fetchone():
        raise HTTPException(409, f"Category '{new_path}' already exists")

    # 1. Update this category
    cur.execute(
        "UPDATE category SET name = %s, full_path = %s WHERE id = %s",
        (body.new_name, new_path, str(category_id)),
    )

    # 2. Cascade to all descendant categories (full_path starts with old_path:)
    old_prefix = old_path + ":"
    new_prefix = new_path + ":"
    cur.execute("""
        UPDATE category
        SET full_path = %s || substring(full_path from %s)
        WHERE full_path LIKE %s
    """, (new_prefix, len(old_prefix) + 1, old_prefix + "%"))
    children_updated = cur.rowcount

    # 3. Cascade to canonical_merchant.category_hint
    cur.execute(
        "UPDATE canonical_merchant SET category_hint = %s WHERE category_hint = %s",
        (new_path, old_path),
    )
    cur.execute("""
        UPDATE canonical_merchant
        SET category_hint = %s || substring(category_hint from %s)
        WHERE category_hint LIKE %s
    """, (new_prefix, len(old_prefix) + 1, old_prefix + "%"))
    merchants_updated = cur.rowcount

    # 4. Cascade to transaction_category_override.category_path
    cur.execute(
        "UPDATE transaction_category_override SET category_path = %s WHERE category_path = %s",
        (new_path, old_path),
    )
    cur.execute("""
        UPDATE transaction_category_override
        SET category_path = %s || substring(category_path from %s)
        WHERE category_path LIKE %s
    """, (new_prefix, len(old_prefix) + 1, old_prefix + "%"))

    # 5. Cascade to transaction_split_line.category_path
    cur.execute(
        "UPDATE transaction_split_line SET category_path = %s WHERE category_path = %s",
        (new_path, old_path),
    )
    cur.execute("""
        UPDATE transaction_split_line
        SET category_path = %s || substring(category_path from %s)
        WHERE category_path LIKE %s
    """, (new_prefix, len(old_prefix) + 1, old_prefix + "%"))

    conn.commit()

    return {
        "id": str(category_id),
        "old_path": old_path,
        "new_path": new_path,
        "children_updated": children_updated,
        "merchants_updated": merchants_updated,
        "renamed": True,
    }


@router.delete("/categories/{category_id}")
def delete_category(category_id: UUID, body: CategoryDelete, conn=Depends(get_conn), user: CurrentUser = Depends(require_admin)):
    """Delete a category, reassigning all references to another category."""
    cur = conn.cursor()

    cur.execute("SELECT full_path, name FROM category WHERE id = %s", (str(category_id),))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Category not found")
    old_path, old_name = row

    if body.reassign_to == category_id:
        raise HTTPException(400, "Cannot reassign to itself")

    cur.execute("SELECT full_path FROM category WHERE id = %s", (str(body.reassign_to),))
    target_row = cur.fetchone()
    if not target_row:
        raise HTTPException(404, "Target category not found")
    target_path = target_row[0]

    # 1. Reassign canonical_merchant.category_hint
    cur.execute(
        "UPDATE canonical_merchant SET category_hint = %s WHERE category_hint = %s",
        (target_path, old_path),
    )
    merchants_moved = cur.rowcount

    # Also reassign children's merchants
    old_prefix = old_path + ":"
    cur.execute("""
        UPDATE canonical_merchant
        SET category_hint = %s
        WHERE category_hint LIKE %s
    """, (target_path, old_prefix + "%"))
    merchants_moved += cur.rowcount

    # 2. Reassign transaction_category_override.category_path
    cur.execute(
        "UPDATE transaction_category_override SET category_path = %s WHERE category_path = %s",
        (target_path, old_path),
    )
    cur.execute("""
        UPDATE transaction_category_override
        SET category_path = %s
        WHERE category_path LIKE %s
    """, (target_path, old_prefix + "%"))

    # 2b. Reassign transaction_split_line.category_path
    cur.execute(
        "UPDATE transaction_split_line SET category_path = %s WHERE category_path = %s",
        (target_path, old_path),
    )
    cur.execute("""
        UPDATE transaction_split_line
        SET category_path = %s
        WHERE category_path LIKE %s
    """, (target_path, old_prefix + "%"))

    # 3. Reassign category_suggestion
    cur.execute(
        "UPDATE category_suggestion SET suggested_category_id = %s WHERE suggested_category_id = %s",
        (str(body.reassign_to), str(category_id)),
    )

    # 3b. Reassign source_category_mapping
    cur.execute(
        "UPDATE source_category_mapping SET category_id = %s WHERE category_id = %s",
        (str(body.reassign_to), str(category_id)),
    )
    cur.execute("""
        UPDATE source_category_mapping SET category_id = %s
        WHERE category_id IN (SELECT id FROM category WHERE full_path LIKE %s)
    """, (str(body.reassign_to), old_prefix + "%"))

    # 4. Reparent child categories to the deleted category's parent
    cur.execute("SELECT parent_id FROM category WHERE id = %s", (str(category_id),))
    parent_id = cur.fetchone()[0]

    # Delete all descendant categories (they've been reassigned)
    cur.execute("DELETE FROM category WHERE full_path LIKE %s", (old_prefix + "%",))
    children_deleted = cur.rowcount

    # 5. Delete the category itself
    cur.execute("DELETE FROM category WHERE id = %s", (str(category_id),))

    conn.commit()

    return {
        "id": str(category_id),
        "deleted_path": old_path,
        "reassigned_to": target_path,
        "merchants_moved": merchants_moved,
        "children_deleted": children_deleted,
    }
