"""Cross-source deduplication matcher.

Identifies duplicate raw_transactions across sources and creates
dedup_group records. Idempotent — skips already-grouped records.

Three rules:
1. source_superseded — blanket suppression of an unreliable source for
   an account where another source is authoritative.
2. cross_source_date_amount — matches same transaction from different sources
   by (institution, account_ref, posted_at, amount, currency).
3. ibank_internal — matches iBank's own internal duplicates
   (same source, same date+amount+merchant).
"""

from typing import Dict, List, Optional, Tuple
from uuid import UUID

from src.dedup.config import CROSS_SOURCE_PAIRS, INTERNAL_DEDUP_SOURCES, SOURCE_SUPERSEDED, get_priority


def find_superseded_transactions(
    conn,
    institution: str,
    account_ref: str,
    superseded_source: str,
) -> List[UUID]:
    """Find transactions from a superseded source that aren't already suppressed.

    Only suppresses transactions whose posted_at >= the earliest date of any
    other (non-superseded) source for the same institution/account. This
    preserves historical data that predates the superseding source's coverage.

    Returns list of raw_transaction IDs to be marked non-preferred.
    """
    cur = conn.cursor()

    # Resolve aliases so we find coverage across all refs for this account
    alias_refs = [account_ref]
    cur.execute(
        "SELECT canonical_ref FROM account_alias WHERE institution = %s AND account_ref = %s",
        (institution, account_ref),
    )
    row = cur.fetchone()
    if row:
        alias_refs.append(row[0])
    cur.execute(
        "SELECT account_ref FROM account_alias WHERE institution = %s AND canonical_ref = %s",
        (institution, account_ref),
    )
    for row in cur.fetchall():
        alias_refs.append(row[0])

    # Find the date range covered by other (superseding) sources for this account
    cur.execute("""
        SELECT MIN(posted_at), MAX(posted_at)
        FROM raw_transaction
        WHERE institution = %(inst)s
          AND account_ref = ANY(%(refs)s)
          AND source != %(src)s
    """, {
        "inst": institution,
        "refs": alias_refs,
        "src": superseded_source,
    })
    row = cur.fetchone()
    coverage_start = row[0] if row and row[0] else None
    coverage_end = row[1] if row and row[1] else None

    if coverage_start is None:
        # No other source exists — nothing to suppress against
        return []

    cur.execute("""
        SELECT rt.id
        FROM raw_transaction rt
        WHERE rt.institution = %(inst)s
          AND rt.account_ref = %(acct)s
          AND rt.source = %(src)s
          AND rt.posted_at >= %(coverage_start)s
          AND rt.posted_at <= %(coverage_end)s
          AND NOT EXISTS (
              SELECT 1 FROM dedup_group_member dgm
              WHERE dgm.raw_transaction_id = rt.id
                AND NOT dgm.is_preferred
          )
        ORDER BY rt.posted_at, rt.id
    """, {
        "inst": institution,
        "acct": account_ref,
        "src": superseded_source,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
    })
    return [row[0] for row in cur.fetchall()]


def suppress_superseded(
    conn,
    institution: str,
    account_ref: str,
    superseded_source: str,
    dry_run: bool = False,
) -> int:
    """Mark all transactions from a superseded source as non-preferred.

    Uses a single SQL statement to create dedup groups and members
    in bulk.  Each superseded txn gets its own single-member group
    with is_preferred=false, which the active_transaction view excludes.

    Returns count of transactions suppressed.
    """
    ids = find_superseded_transactions(conn, institution, account_ref, superseded_source)
    if not ids or dry_run:
        return len(ids)

    cur = conn.cursor()

    # Flip any existing preferred memberships to non-preferred
    cur.execute("""
        UPDATE dedup_group_member SET is_preferred = false
        WHERE raw_transaction_id = ANY(%s::uuid[])
          AND is_preferred = true
    """, ([str(i) for i in ids],))

    # Filter out IDs that are already in a group
    cur.execute("""
        SELECT raw_transaction_id FROM dedup_group_member
        WHERE raw_transaction_id = ANY(%s::uuid[])
    """, ([str(i) for i in ids],))
    already_grouped = {row[0] for row in cur.fetchall()}
    new_ids = [i for i in ids if i not in already_grouped]

    # Use a CTE to create groups and members in one statement
    if new_ids:
        cur.execute("""
            WITH new_groups AS (
                INSERT INTO dedup_group (canonical_id, match_rule, confidence)
                SELECT id, 'source_superseded', 1.0
                FROM raw_transaction
                WHERE id = ANY(%(ids)s::uuid[])
                RETURNING id AS group_id, canonical_id AS txn_id
            )
            INSERT INTO dedup_group_member (dedup_group_id, raw_transaction_id, is_preferred)
            SELECT group_id, txn_id, false
            FROM new_groups
        """, {"ids": [str(i) for i in new_ids]})

    conn.commit()
    return len(ids)


def suppress_declined(conn, dry_run: bool = False) -> int:
    """Suppress Monzo declined transactions (amount in raw_data is 0 or decline_reason set).

    These are API-recorded attempted charges that never settled.
    Returns count of transactions suppressed.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT rt.id
        FROM raw_transaction rt
        WHERE rt.source = 'monzo_api'
          AND rt.raw_data->>'decline_reason' IS NOT NULL
          AND rt.raw_data->>'decline_reason' != ''
          AND NOT EXISTS (
              SELECT 1 FROM dedup_group_member dgm
              WHERE dgm.raw_transaction_id = rt.id
                AND NOT dgm.is_preferred
          )
    """)
    ids = [row[0] for row in cur.fetchall()]
    if not ids or dry_run:
        return len(ids)

    # Flip any existing preferred memberships to non-preferred
    cur.execute("""
        UPDATE dedup_group_member SET is_preferred = false
        WHERE raw_transaction_id = ANY(%s::uuid[])
          AND is_preferred = true
    """, ([str(i) for i in ids],))

    # Filter out IDs that are already in a group (as non-preferred now)
    cur.execute("""
        SELECT raw_transaction_id FROM dedup_group_member
        WHERE raw_transaction_id = ANY(%s::uuid[])
    """, ([str(i) for i in ids],))
    already_grouped = {row[0] for row in cur.fetchall()}
    new_ids = [i for i in ids if i not in already_grouped]

    if new_ids:
        cur.execute("""
            WITH new_groups AS (
                INSERT INTO dedup_group (canonical_id, match_rule, confidence)
                SELECT id, 'declined', 1.0
                FROM raw_transaction
                WHERE id = ANY(%(ids)s::uuid[])
                RETURNING id AS group_id, canonical_id AS txn_id
            )
            INSERT INTO dedup_group_member (dedup_group_id, raw_transaction_id, is_preferred)
            SELECT group_id, txn_id, false
            FROM new_groups
        """, {"ids": [str(i) for i in new_ids]})

    conn.commit()
    return len(ids)


def resolve_account_ref(
    conn, institution: str, account_ref: str
) -> str:
    """Resolve account_ref via account_alias table.

    Returns canonical_ref if alias exists, otherwise the original.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT canonical_ref FROM account_alias WHERE institution = %s AND account_ref = %s",
        (institution, account_ref),
    )
    row = cur.fetchone()
    return row[0] if row else account_ref


def find_cross_source_duplicates(
    conn,
    institution: str,
    account_ref: str,
    source_a: str,
    source_b: str,
    date_tolerance: int = 0,
) -> List[tuple]:
    """Find cross-source duplicate pairs by date+amount.

    Uses ROW_NUMBER() positional matching within date+amount buckets
    to handle multiple same-day same-amount transactions.

    Args:
        date_tolerance: max days apart to consider a match (0 = exact).

    Returns list of (id_a, source_a, id_b, source_b) tuples.
    IDs are UUID objects (not strings).
    """
    cur = conn.cursor()

    # Resolve aliases for the account_ref
    alias_refs = [account_ref]
    cur.execute(
        "SELECT account_ref FROM account_alias WHERE institution = %s AND canonical_ref = %s",
        (institution, account_ref),
    )
    for row in cur.fetchall():
        alias_refs.append(row[0])

    cur.execute("""
        WITH candidates AS (
            SELECT
                rt.id,
                rt.source,
                rt.posted_at,
                rt.amount,
                rt.currency,
                ROW_NUMBER() OVER (
                    PARTITION BY rt.source, rt.posted_at, rt.amount, rt.currency
                    ORDER BY rt.id
                ) AS pos
            FROM raw_transaction rt
            WHERE rt.institution = %(inst)s
              AND rt.account_ref = ANY(%(refs)s)
              AND rt.source IN (%(src_a)s, %(src_b)s)
              AND NOT EXISTS (
                  SELECT 1 FROM dedup_group_member dgm
                  WHERE dgm.raw_transaction_id = rt.id
                    AND NOT dgm.is_preferred
              )
        )
        SELECT a.id, a.source, b.id, b.source
        FROM candidates a
        JOIN candidates b
            ON ABS(a.posted_at - b.posted_at) <= %(date_tolerance)s
            AND a.amount = b.amount
            AND a.currency = b.currency
            AND a.source = %(src_a)s
            AND b.source = %(src_b)s
            AND a.pos = b.pos
        ORDER BY a.posted_at, a.amount
    """, {
        "inst": institution,
        "refs": alias_refs,
        "src_a": source_a,
        "src_b": source_b,
        "date_tolerance": date_tolerance,
    })

    return cur.fetchall()  # (uuid, str, uuid, str) tuples


def find_internal_duplicates(
    conn,
    source: str,
    institution: Optional[str] = None,
) -> List[tuple]:
    """Find internal duplicates for a source (same date+amount+merchant within same account).

    Returns list of (keep_id, dupe_id) tuples as UUID objects.
    """
    cur = conn.cursor()

    where_extra = "AND a.institution = %(inst)s" if institution else ""

    cur.execute(f"""
        SELECT a.id, b.id
        FROM raw_transaction a
        JOIN raw_transaction b
            ON a.institution = b.institution
            AND a.account_ref = b.account_ref
            AND a.posted_at = b.posted_at
            AND a.amount = b.amount
            AND a.currency = b.currency
            AND a.raw_merchant = b.raw_merchant
            AND a.source = b.source
            AND a.id < b.id
        WHERE a.source = %(source)s
          AND NOT EXISTS (
              SELECT 1 FROM dedup_group_member dgm
              WHERE dgm.raw_transaction_id = a.id AND NOT dgm.is_preferred
          )
          AND NOT EXISTS (
              SELECT 1 FROM dedup_group_member dgm
              WHERE dgm.raw_transaction_id = b.id AND NOT dgm.is_preferred
          )
          {where_extra}
        ORDER BY a.posted_at, a.amount
    """, {"source": source, "inst": institution} if institution else {"source": source})

    return cur.fetchall()  # (uuid, uuid) tuples


def _check_already_grouped(cur, ids: list) -> set:
    """Check which of the given UUIDs are already in a dedup group."""
    cur.execute(
        "SELECT raw_transaction_id FROM dedup_group_member WHERE raw_transaction_id = ANY(%s::uuid[])",
        (ids,),
    )
    return {r[0] for r in cur.fetchall()}


def create_dedup_group(
    conn,
    member_ids_with_source: List[tuple],
    match_rule: str,
    confidence: float = 1.0,
) -> Optional[str]:
    """Create a dedup group with members.

    Args:
        member_ids_with_source: list of (raw_transaction_id, source) tuples
        match_rule: rule name
        confidence: 0.0-1.0

    Returns group ID, or None if any member already grouped.
    """
    cur = conn.cursor()

    ids = [mid for mid, _ in member_ids_with_source]
    existing = _check_already_grouped(cur, ids)
    if existing:
        return None

    # Determine preferred by source priority
    best = min(member_ids_with_source, key=lambda x: get_priority(x[1]))
    preferred_id = best[0]

    # Create group
    cur.execute(
        """INSERT INTO dedup_group (canonical_id, match_rule, confidence)
           VALUES (%s, %s, %s) RETURNING id""",
        (preferred_id, match_rule, confidence),
    )
    group_id = cur.fetchone()[0]

    # Add members
    for mid, source in member_ids_with_source:
        cur.execute(
            """INSERT INTO dedup_group_member (dedup_group_id, raw_transaction_id, is_preferred)
               VALUES (%s, %s, %s)""",
            (group_id, mid, mid == preferred_id),
        )

    return group_id


def extend_dedup_group(
    conn,
    existing_member_id,
    new_member_id,
    new_source: str,
) -> Optional[str]:
    """Add a new member to an existing dedup group.

    If the new member has higher priority than the current preferred,
    it becomes the new preferred and the group's canonical_id is updated.
    """
    cur = conn.cursor()

    # Check new member isn't already a non-preferred member of another group
    cur.execute(
        """SELECT dedup_group_id, is_preferred FROM dedup_group_member
           WHERE raw_transaction_id = %s""",
        (new_member_id,),
    )
    row = cur.fetchone()
    if row and not row[1]:
        # Already a non-preferred member elsewhere — skip
        return None
    if row and row[1]:
        # Already preferred in another group — skip (would need merge logic)
        return None

    # Find the existing group
    cur.execute(
        "SELECT dedup_group_id FROM dedup_group_member WHERE raw_transaction_id = %s",
        (existing_member_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    group_id = row[0]

    # Get the current preferred member's source
    cur.execute(
        """SELECT dgm.raw_transaction_id, rt.source
           FROM dedup_group_member dgm
           JOIN raw_transaction rt ON rt.id = dgm.raw_transaction_id
           WHERE dgm.dedup_group_id = %s AND dgm.is_preferred = true""",
        (group_id,),
    )
    current_preferred = cur.fetchone()

    new_priority = get_priority(new_source)
    current_priority = get_priority(current_preferred[1]) if current_preferred else 99

    if new_priority < current_priority:
        # New member has higher priority — it becomes preferred
        cur.execute(
            """UPDATE dedup_group_member SET is_preferred = false
               WHERE dedup_group_id = %s AND is_preferred = true""",
            (group_id,),
        )
        cur.execute(
            """INSERT INTO dedup_group_member (dedup_group_id, raw_transaction_id, is_preferred)
               VALUES (%s, %s, true)""",
            (group_id, new_member_id),
        )
        cur.execute(
            "UPDATE dedup_group SET canonical_id = %s WHERE id = %s",
            (new_member_id, group_id),
        )
    else:
        # Existing preferred stays — add new as non-preferred
        cur.execute(
            """INSERT INTO dedup_group_member (dedup_group_id, raw_transaction_id, is_preferred)
               VALUES (%s, %s, false)""",
            (group_id, new_member_id),
        )

    return str(group_id)


def find_duplicates(
    conn,
    institution: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Run all matching rules. Main entry point."""
    stats = {
        "source_superseded": 0,
        "declined": 0,
        "cross_source_groups": 0,
        "cross_source_extended": 0,
        "ibank_internal_groups": 0,
        "skipped": 0,
    }

    cur = conn.cursor()

    # Rule 0: Source supersession (run FIRST — blanket suppression
    # before any pair matching)
    print("  Source supersession:")
    for config in SOURCE_SUPERSEDED:
        inst = config["institution"]
        acct = config["account_ref"]
        src = config["superseded_source"]

        if institution and inst != institution:
            continue

        count = suppress_superseded(conn, inst, acct, src, dry_run=dry_run)
        if count:
            print(f"    {inst}/{acct}: suppressed {count} {src} transactions")
            stats["source_superseded"] += count

    if not stats["source_superseded"]:
        print("    (none)")

    # Rule 0b: Declined transactions
    declined = suppress_declined(conn, dry_run=dry_run)
    if declined:
        print(f"  Declined transactions: suppressed {declined}")
        stats["declined"] = declined

    print()

    # Rule 1: Internal duplicates (run FIRST so cross-source
    # matching sees consolidated records, not dupes)
    for source in INTERNAL_DEDUP_SOURCES:
        pairs = find_internal_duplicates(conn, source, institution=institution)
        if not pairs:
            continue

        print(f"  {source} internal duplicates:")
        print(f"    Found {len(pairs)} pairs")

        if not dry_run:
            for keep_id, dupe_id in pairs:
                gid = create_dedup_group(
                    conn,
                    [(keep_id, source), (dupe_id, source)],
                    "ibank_internal",
                    confidence=0.95,
                )
                if gid:
                    stats["ibank_internal_groups"] += 1
                else:
                    stats["skipped"] += 1
            conn.commit()
        else:
            stats["ibank_internal_groups"] += len(pairs)

    # Rule 2: Cross-source date+amount matching
    print()
    for config in CROSS_SOURCE_PAIRS:
        inst = config["institution"]
        acct = config["account_ref"]

        if institution and inst != institution:
            continue

        for source_a, source_b in config["pairs"]:
            tolerance = config.get("date_tolerance", 0)
            pairs = find_cross_source_duplicates(conn, inst, acct, source_a, source_b, date_tolerance=tolerance)

            if not pairs:
                continue

            print(f"  {inst}/{acct} {source_a} <-> {source_b}: {len(pairs)} matches")

            if dry_run:
                stats["cross_source_groups"] += len(pairs)
                continue

            for id_a, src_a, id_b, src_b in pairs:
                already = _check_already_grouped(cur, [id_a, id_b])

                if id_a in already and id_b in already:
                    stats["skipped"] += 1
                elif id_a in already:
                    gid = extend_dedup_group(conn, id_a, id_b, src_b)
                    if gid:
                        stats["cross_source_extended"] += 1
                    else:
                        stats["skipped"] += 1
                elif id_b in already:
                    gid = extend_dedup_group(conn, id_b, id_a, src_a)
                    if gid:
                        stats["cross_source_extended"] += 1
                    else:
                        stats["skipped"] += 1
                else:
                    gid = create_dedup_group(
                        conn,
                        [(id_a, src_a), (id_b, src_b)],
                        "cross_source_date_amount",
                    )
                    if gid:
                        stats["cross_source_groups"] += 1
                    else:
                        stats["skipped"] += 1

            conn.commit()

    return stats


def show_stats(conn) -> None:
    """Print dedup statistics."""
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM dedup_group")
    total_groups = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM dedup_group_member")
    total_members = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM dedup_group_member WHERE is_preferred")
    preferred = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM raw_transaction")
    total_raw = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM active_transaction")
    total_active = cur.fetchone()[0]

    print(f"  Dedup groups:      {total_groups}")
    print(f"  Group members:     {total_members}")
    print(f"  Preferred:         {preferred}")
    print(f"  Raw transactions:  {total_raw}")
    print(f"  Active (deduped):  {total_active}")
    print(f"  Removed by dedup:  {total_raw - total_active}")

    # By rule
    cur.execute("""
        SELECT match_rule, count(*) as groups,
               (SELECT count(*) FROM dedup_group_member dgm2
                JOIN dedup_group dg2 ON dg2.id = dgm2.dedup_group_id
                WHERE dg2.match_rule = dg.match_rule) as members
        FROM dedup_group dg
        GROUP BY match_rule
    """)
    rows = cur.fetchall()
    if rows:
        print(f"\n  By rule:")
        for r in rows:
            print(f"    {r[0]:30s} {r[1]:>5} groups, {r[2]:>5} members")

    # Remaining overlap check
    cur.execute("""
        SELECT a.institution, a.account_ref, a.source, b.source, count(*)
        FROM active_transaction a
        JOIN active_transaction b
          ON a.institution = b.institution
          AND a.account_ref = b.account_ref
          AND a.posted_at = b.posted_at
          AND a.amount = b.amount
          AND a.currency = b.currency
          AND a.source < b.source
          AND a.id != b.id
        GROUP BY a.institution, a.account_ref, a.source, b.source
        ORDER BY count(*) DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    if rows:
        print(f"\n  Remaining cross-source overlaps in active_transaction:")
        for r in rows:
            print(f"    {r[0]:15s} {r[1]:20s} {r[2]} <-> {r[3]}: {r[4]}")
    else:
        print(f"\n  No remaining cross-source overlaps!")


def reset_groups(conn) -> int:
    """Delete all dedup groups. Returns count deleted."""
    cur = conn.cursor()
    cur.execute("DELETE FROM dedup_group")
    count = cur.rowcount
    conn.commit()
    return count
