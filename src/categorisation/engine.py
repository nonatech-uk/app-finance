"""Categorisation engine — orchestrates naming and auto-categorisation.

Phases:
0. Regex rules: apply merchant_display_rule patterns (merge + rename + categorise)
1. Naming: set display_name from iBank merchant names
2. Source hints: extract category hints from iBank/Monzo/Wise raw_data
3. LLM categorisation for remaining uncategorised merchants (auto-accept >= 0.85)
4. Fuzzy merchant matching: merge uncategorised merchants with near-duplicates
5. Amazon per-transaction categorisation via Haiku
6. Override learning: detect patterns in user overrides
"""

import re

import psycopg2

from src.categorisation.source_hints import extract_hints, extract_display_names
from src.categorisation.merger import merge


AUTO_ACCEPT_THRESHOLD = 0.85


def run_regex_rules(conn, *, dry_run: bool = False) -> dict:
    """Phase 0: Apply regex display rules — rename, merge, and optionally categorise."""
    cur = conn.cursor()

    # Load rules
    cur.execute("""
        SELECT id, pattern, display_name, merge_group, category_hint
        FROM merchant_display_rule
        ORDER BY priority, id
    """)
    rules = cur.fetchall()
    if not rules:
        return {"rules_applied": 0, "merchants_renamed": 0, "merchants_merged": 0}

    # Load all unmerged merchants
    cur.execute("""
        SELECT id, name, display_name, category_hint
        FROM canonical_merchant
        WHERE merged_into_id IS NULL
        ORDER BY name
    """)
    merchants = cur.fetchall()

    total_renamed = 0
    total_merged = 0
    total_categorised = 0

    for rule_id, pattern, rule_display, merge_group, rule_category in rules:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            print(f"  WARNING: Skipping rule '{pattern}' — invalid regex: {e}")
            continue
        matched = [(str(m[0]), m[1], m[2], m[3]) for m in merchants if regex.match(m[1])]

        if not matched:
            continue

        print(f"  Rule '{pattern}' -> '{rule_display}': {len(matched)} matches")

        if dry_run:
            for mid, mname, _, _ in matched[:5]:
                print(f"    {mname}")
            if len(matched) > 5:
                print(f"    ... and {len(matched) - 5} more")
            continue

        # Pick or create the surviving merchant for merge
        # Prefer one that already has this display_name, else pick first match
        surviving_id = None
        for mid, mname, mdisplay, mcat in matched:
            if mdisplay == rule_display or mname == rule_display:
                surviving_id = mid
                break
        if not surviving_id:
            surviving_id = matched[0][0]

        # Set display_name on surviving
        cur.execute(
            "UPDATE canonical_merchant SET display_name = %s WHERE id = %s",
            (rule_display, surviving_id),
        )
        total_renamed += 1

        # Set category if rule specifies one and surviving has none
        if rule_category:
            cur.execute(
                """UPDATE canonical_merchant
                   SET category_hint = %s, category_method = 'rule', category_confidence = 0.95,
                       category_set_at = now()
                   WHERE id = %s AND category_hint IS NULL""",
                (rule_category, surviving_id),
            )
            if cur.rowcount:
                total_categorised += 1

        # Merge all others into surviving
        if merge_group and len(matched) > 1:
            for mid, mname, _, _ in matched:
                if mid != surviving_id:
                    try:
                        merge(conn, secondary_id=mid, surviving_id=surviving_id)
                        total_merged += 1
                    except ValueError as e:
                        print(f"    Merge error for {mname}: {e}")

    if not dry_run:
        conn.commit()

    print(f"  Regex rules: {total_renamed} renamed, {total_merged} merged, {total_categorised} categorised")
    return {"rules_applied": len(rules), "merchants_renamed": total_renamed, "merchants_merged": total_merged, "merchants_categorised": total_categorised}


def run_naming(conn, *, dry_run: bool = False) -> dict:
    """Phase 1: Apply regex rules, then set display_name from iBank."""
    # Phase 0: regex rules first
    rules_result = run_regex_rules(conn, dry_run=dry_run)

    # Phase 1: iBank display names
    names = extract_display_names(conn)
    print(f"  Found {len(names)} display name candidates from iBank")

    if dry_run:
        for n in names[:30]:
            print(f"    {n['current_name']:<40} -> {n['display_name']}")
        if len(names) > 30:
            print(f"    ... and {len(names) - 30} more")
        return {
            "display_names_set": 0,
            "candidates": len(names),
            **{f"rules_{k}": v for k, v in rules_result.items()},
        }

    cur = conn.cursor()
    updated = 0
    for n in names:
        cur.execute(
            "UPDATE canonical_merchant SET display_name = %s WHERE id = %s AND display_name IS NULL",
            (n['display_name'], n['canonical_merchant_id']),
        )
        updated += cur.rowcount

    conn.commit()
    print(f"  Set {updated} display names from iBank")
    return {
        "display_names_set": updated,
        "candidates": len(names),
        **{f"rules_{k}": v for k, v in rules_result.items()},
    }


def run_source_hints(conn, *, dry_run: bool = False) -> dict:
    """Phase 2: Extract source hints and auto-accept or queue suggestions."""
    suggestions = extract_hints(conn)
    print(f"  Found {len(suggestions)} source hint suggestions")

    auto_accepted = [s for s in suggestions if s['confidence'] >= AUTO_ACCEPT_THRESHOLD]
    queued = [s for s in suggestions if s['confidence'] < AUTO_ACCEPT_THRESHOLD]

    print(f"  Auto-accept (>={AUTO_ACCEPT_THRESHOLD}): {len(auto_accepted)}")
    print(f"  Queue for review: {len(queued)}")

    if dry_run:
        print("\n  === Auto-accept ===")
        for s in auto_accepted[:20]:
            print(f"    {s['merchant_name']:<40} -> {s['suggested_category_path']:<30} ({s['confidence']:.0%} {s['reasoning']})")
        if len(auto_accepted) > 20:
            print(f"    ... and {len(auto_accepted) - 20} more")

        print("\n  === Queue for review ===")
        for s in queued[:20]:
            print(f"    {s['merchant_name']:<40} -> {s['suggested_category_path']:<30} ({s['confidence']:.0%} {s['reasoning']})")
        if len(queued) > 20:
            print(f"    ... and {len(queued) - 20} more")

        return {"auto_accepted": 0, "queued": 0, "total_suggestions": len(suggestions)}

    cur = conn.cursor()

    # Auto-accept high confidence
    accepted_count = 0
    for s in auto_accepted:
        cur.execute("""
            UPDATE canonical_merchant
            SET category_hint = (SELECT full_path FROM category WHERE id = %s),
                category_method = %s,
                category_confidence = %s,
                category_set_at = now()
            WHERE id = %s AND category_hint IS NULL
        """, (s['suggested_category_id'], s['method'], s['confidence'], s['canonical_merchant_id']))
        accepted_count += cur.rowcount

        # Also set display_name if we have a candidate and merchant has none
        if s.get('display_name_candidate'):
            cur.execute(
                "UPDATE canonical_merchant SET display_name = %s WHERE id = %s AND display_name IS NULL",
                (s['display_name_candidate'], s['canonical_merchant_id']),
            )

    # Queue medium confidence as suggestions
    queued_count = 0
    for s in queued:
        cur.execute("""
            INSERT INTO category_suggestion
                (canonical_merchant_id, suggested_category_id, method, confidence, reasoning)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (s['canonical_merchant_id'], s['suggested_category_id'], s['method'], s['confidence'], s['reasoning']))
        queued_count += cur.rowcount

    conn.commit()
    print(f"  Applied {accepted_count} auto-accepted categories")
    print(f"  Queued {queued_count} suggestions for review")

    return {"auto_accepted": accepted_count, "queued": queued_count, "total_suggestions": len(suggestions)}


def run_llm(conn, *, dry_run: bool = False) -> dict:
    """Phase 3: LLM categorisation for remaining uncategorised merchants.

    categorise_batch now persists per-batch internally.
    """
    try:
        from src.categorisation.llm_categoriser import categorise_batch
    except ImportError:
        print("  LLM categoriser not available (missing anthropic package?)")
        return {"llm_auto_accepted": 0, "llm_queued": 0}

    return categorise_batch(conn, dry_run=dry_run)


def run_fuzzy_merge(conn, *, dry_run: bool = False) -> dict:
    """Phase 4: Fuzzy match uncategorised merchants against categorised ones."""
    from src.categorisation.fuzzy_matcher import find_fuzzy_matches
    return find_fuzzy_matches(conn, dry_run=dry_run)


def run_amazon(conn, *, dry_run: bool = False) -> dict:
    """Phase 5: Per-transaction Amazon categorisation via Haiku."""
    from src.categorisation.amazon_categoriser import categorise_amazon_transactions
    return categorise_amazon_transactions(conn, dry_run=dry_run)


def run_override_learning(conn, *, dry_run: bool = False) -> dict:
    """Phase 6: Learn from user overrides and apply to future transactions."""
    from src.categorisation.override_learner import learn_from_overrides
    return learn_from_overrides(conn, dry_run=dry_run)


def run_all(conn, *, dry_run: bool = False, include_llm: bool = True,
            include_amazon: bool = True) -> dict:
    """Run full categorisation pipeline: all phases.

    Args:
        dry_run: Preview changes without writing.
        include_llm: Include LLM categorisation (requires ANTHROPIC_API_KEY).
        include_amazon: Include Amazon per-transaction categorisation.
    """
    results = {}

    print("Phase 0+1: Regex rules + naming...")
    results['naming'] = run_naming(conn, dry_run=dry_run)

    print("\nPhase 2: Source hints...")
    results['source_hints'] = run_source_hints(conn, dry_run=dry_run)

    if include_llm:
        print("\nPhase 3: LLM categorisation...")
        results['llm'] = run_llm(conn, dry_run=dry_run)

    print("\nPhase 4: Fuzzy merchant matching...")
    results['fuzzy'] = run_fuzzy_merge(conn, dry_run=dry_run)

    if include_amazon:
        print("\nPhase 5: Amazon per-transaction categorisation...")
        results['amazon'] = run_amazon(conn, dry_run=dry_run)

    print("\nPhase 6: Override learning...")
    results['overrides'] = run_override_learning(conn, dry_run=dry_run)

    return results
