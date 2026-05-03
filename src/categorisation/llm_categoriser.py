"""LLM-based categorisation using Anthropic Claude.

Batches uncategorised merchants and asks Claude Haiku to suggest categories
from the existing hierarchy. Persists results after each batch so progress
is never lost.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from config.settings import settings


BATCH_SIZE = 25
AUTO_ACCEPT_THRESHOLD = 0.85

# Default window for daily sync — only categorise merchants created in the last 48h.
# Pass since=None to process the full backlog (e.g. for a one-off catchup).
DEFAULT_LOOKBACK_HOURS = 48


def categorise_batch(conn, *, dry_run: bool = False, since: datetime | None = "default",
                     verbose: bool = False) -> dict:
    """Run LLM categorisation for uncategorised merchants.

    Persists results (auto-accept or queue) after each batch. Returns
    a summary dict with counts.

    Args:
        since: Only process merchants created after this datetime.
               Defaults to 48h ago for daily sync. Pass None to process
               the full backlog.
        verbose: Print each suggestion as it's made.
    """
    if since == "default":
        since = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    if not settings.anthropic_api_key:
        print("  ANTHROPIC_API_KEY not set — skipping LLM categorisation")
        return {"llm_auto_accepted": 0, "llm_queued": 0}

    try:
        import anthropic
    except ImportError:
        print("  anthropic package not installed — run: pip install anthropic")
        return {"llm_auto_accepted": 0, "llm_queued": 0}

    cur = conn.cursor()

    # Get the full category tree
    cur.execute("SELECT id, full_path FROM category WHERE is_active = true ORDER BY full_path")
    categories = [(str(r[0]), r[1]) for r in cur.fetchall()]
    category_list = "\n".join(f"- {path}" for _, path in categories)
    cat_id_by_path = {path: cid for cid, path in categories}

    # Find uncategorised, unmerged, non-Amazon merchants
    if since is not None:
        cur.execute("""
            SELECT cm.id, cm.name, cm.display_name
            FROM canonical_merchant cm
            WHERE cm.category_hint IS NULL
              AND cm.merged_into_id IS NULL
              AND cm.created_at >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM category_suggestion cs
                  WHERE cs.canonical_merchant_id = cm.id
              )
            ORDER BY cm.name
        """, (since,))
    else:
        cur.execute("""
            SELECT cm.id, cm.name, cm.display_name
            FROM canonical_merchant cm
            WHERE cm.category_hint IS NULL
              AND cm.merged_into_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM category_suggestion cs
                  WHERE cs.canonical_merchant_id = cm.id
              )
            ORDER BY cm.name
        """)
    merchants = [(str(r[0]), r[1], r[2]) for r in cur.fetchall()]

    # Filter out Amazon
    merchants = [(mid, name, dname) for mid, name, dname in merchants
                 if not _is_amazon(name)]

    if not merchants:
        print("  No uncategorised merchants remaining for LLM")
        return {"llm_auto_accepted": 0, "llm_queued": 0}

    print(f"  {len(merchants)} merchants to categorise via LLM")

    if dry_run:
        print(f"  Would process {len(merchants)} merchants in {(len(merchants) + BATCH_SIZE - 1) // BATCH_SIZE} batches")
        for m in merchants[:10]:
            print(f"    {m[1]}")
        if len(merchants) > 10:
            print(f"    ... and {len(merchants) - 10} more")
        return {"llm_auto_accepted": 0, "llm_queued": 0}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    total_accepted = 0
    total_queued = 0

    for i in range(0, len(merchants), BATCH_SIZE):
        batch = merchants[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(merchants) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} merchants)...")

        merchant_lines = []
        merchant_names = {}
        for mid, name, dname in batch:
            display = f" (display: {dname})" if dname else ""
            merchant_lines.append(f"- ID:{mid} | {name}{display}")
            merchant_names[mid] = dname or name
        merchant_text = "\n".join(merchant_lines)

        prompt = f"""You are categorising merchants for a personal finance system.

For each merchant below, suggest the single best matching category from the category tree.
If you're not confident, say "SKIP".

CATEGORY TREE:
{category_list}

MERCHANTS TO CATEGORISE:
{merchant_text}

Respond with a JSON array. Each element must have:
- "id": the merchant ID (the UUID only, without the ID: prefix)
- "category": the exact full_path from the category tree, or "SKIP"
- "confidence": a number 0.0-1.0
- "reasoning": brief explanation (max 20 words)

Respond ONLY with the JSON array, no other text."""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            # Handle potential markdown wrapping
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3].strip()

            results = json.loads(text)

            batch_accepted = 0
            batch_queued = 0
            for result in results:
                mid = str(result.get("id", "")).removeprefix("ID:")
                try:
                    uuid.UUID(mid)
                except ValueError:
                    continue

                cat_path = result.get("category", "SKIP")
                confidence = float(result.get("confidence", 0))
                reasoning = result.get("reasoning", "")

                if cat_path == "SKIP" or confidence < 0.3:
                    continue

                cat_id = cat_id_by_path.get(cat_path)
                if not cat_id:
                    continue

                if verbose:
                    icon = "A" if confidence >= AUTO_ACCEPT_THRESHOLD else "Q"
                    name = merchant_names.get(mid, mid)
                    print(f"    [{icon}] {name:40s} -> {cat_path:40s} ({confidence:.0%})")

                # Persist immediately
                if confidence >= AUTO_ACCEPT_THRESHOLD:
                    cur.execute("""
                        UPDATE canonical_merchant
                        SET category_hint = (SELECT full_path FROM category WHERE id = %s),
                            category_method = 'llm',
                            category_confidence = %s,
                            category_set_at = now()
                        WHERE id = %s AND category_hint IS NULL
                    """, (cat_id, confidence, mid))
                    batch_accepted += cur.rowcount
                else:
                    cur.execute("""
                        INSERT INTO category_suggestion
                            (canonical_merchant_id, suggested_category_id, method, confidence, reasoning)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (mid, cat_id, 'llm', confidence, f"LLM: {reasoning}"))
                    batch_queued += cur.rowcount

            conn.commit()
            total_accepted += batch_accepted
            total_queued += batch_queued
            print(f"    {batch_accepted} accepted, {batch_queued} queued")

        except json.JSONDecodeError as e:
            print(f"    ERROR: Failed to parse LLM response: {e}")
            conn.rollback()
            continue
        except Exception as e:
            print(f"    ERROR: LLM call failed: {e}")
            conn.rollback()
            continue

    print(f"  Total: {total_accepted} auto-accepted, {total_queued} queued")
    return {"llm_auto_accepted": total_accepted, "llm_queued": total_queued}


def _is_amazon(name: str) -> bool:
    """Check if a merchant name is Amazon-related."""
    lower = name.lower()
    return 'amazon' in lower or 'amzn' in lower or 'amz ' in lower
