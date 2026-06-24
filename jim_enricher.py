#!/usr/bin/env python3
"""
Jim Enricher — Outline Ventures Attio Watchlist Enrichment Script

Requirements:
    pip install requests

Environment variables (set in .env):
    ATTIO_API_KEY   — Attio API key (Settings → Developers)
    SERPER_API_KEY  — Serper.dev key (free tier: 2500/mo)
    SLACK_TOKEN     — Slack OAuth token (optional)

Usage:
    python jim_enricher.py             # enrich entries missing any key field
    python jim_enricher.py --all       # re-enrich every entry
    python jim_enricher.py --new-only  # only entries not yet in state file
    python jim_enricher.py --dry-run   # preview without writing to Attio
"""

import os, json, re, time, argparse, requests
from pathlib import Path
from datetime import datetime

# ─── Config ──────────────────────────────────────────────────────────────────

ATTIO_BASE    = "https://api.attio.com/v2"
SERPER_URL    = "https://google.serper.dev/search"
SLACK_URL     = "https://slack.com/api/search.messages"
WATCHLIST_ID  = "b4a5069b-1ccf-4b04-8737-73f4a686b658"
STATE_FILE    = Path(__file__).parent / "processed_companies.json"

SECTOR_OPTIONS = [
    "Agriculture", "BioTech", "Consumer hardware", "Design", "Enterprise",
    "Retail", "SaaS", "Wholesale", "Circularity", "Logistics", "Restaurant",
    "Retail tech", "Supply Chain", "Materials", "Physical AI", "Fleet",
    "Warehousing", "Micromobility", "Automotive", "Mining",
    "Manufacturing", "Construction",
]

SECTOR_KEYWORDS = {
    "Agriculture":       ["agri", "farm", "crop", "soil", "precision ag", "livestock"],
    "BioTech":           ["biotech", "genomic", "life science", "synthetic bio", "crispr", "drug"],
    "Construction":      ["construction", "building", "concrete", "rebar", "contractor"],
    "Logistics":         ["logistics", "shipping", "freight", "last mile", "delivery"],
    "Manufacturing":     ["manufactur", "factory", "industrial", "fabricat", "production"],
    "SaaS":              ["saas", "software as a service", "b2b software", "cloud platform"],
    "Supply Chain":      ["supply chain", "procurement", "sourcing", "inventory"],
    "Materials":         ["materials", "metals", "steel", "aluminum", "composites"],
    "Automotive":        ["automotive", "vehicle", "ev ", "electric vehicle", "fleet"],
    "Warehousing":       ["warehousing", "warehouse", "fulfillment center"],
    "Enterprise":        ["enterprise software", "enterprise ai", "erp", "hr tech"],
    "Physical AI":       ["robotics", "physical ai", "autonomous robot", "cobots"],
    "Mining":            ["mining", "mineral extraction", "quarry"],
    "Retail tech":       ["retail tech", "point of sale", "pos ", "e-commerce platform"],
    "Retail":            ["retail", "consumer brand", "direct to consumer", "d2c"],
    "Wholesale":         ["wholesale", "distributor", "b2b marketplace"],
    "Fleet":             ["fleet management", "telematics", "fleet tracking"],
    "Micromobility":     ["micromobility", "e-scooter", "e-bike", "bike share"],
    "Consumer hardware": ["consumer hardware", "wearable", "smart device", "iot device"],
    "Circularity":       ["circular economy", "recycling", "upcycling", "waste reduction"],
    "Design":            ["design software", "cad ", "design tool", "creative tool"],
    "Restaurant":        ["restaurant", "foodservice", "food tech", "ghost kitchen"],
}


# ─── Attio helpers ───────────────────────────────────────────────────────────

def attio_headers():
    key = os.environ.get("ATTIO_API_KEY", "")
    if not key:
        raise ValueError("ATTIO_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def get_watchlist_entries():
    entries, offset = [], 0
    while True:
        r = requests.post(
            f"{ATTIO_BASE}/lists/{WATCHLIST_ID}/entries/query",
            headers=attio_headers(),
            json={"limit": 50, "offset": offset},
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get("data", [])
        entries.extend(batch)
        if not data.get("has_more"):
            break
        offset += len(batch)
    return entries


def build_company_lookup():
    """
    Bulk-fetch all company records (no filter) and return a dict:
        record_id -> {"name": str, "domain": str, "description": str}
    Uses a single paginated request — no Object Configuration scope needed.
    """
    lookup, offset, page_size = {}, 0, 500
    print("  Building company name lookup…", end="", flush=True)
    while True:
        r = requests.post(
            f"{ATTIO_BASE}/objects/companies/records/query",
            headers=attio_headers(),
            json={"limit": page_size, "offset": offset},
        )
        if not r.ok:
            print(f"\n  [warn] Could not fetch company records ({r.status_code}): {r.text[:120]}")
            print("  Company names will be unavailable; enrichment will be skipped.")
            return {}
        batch = r.json().get("data", [])
        for rec in batch:
            rid = rec.get("id", {}).get("record_id", "")
            values = rec.get("values", {})

            name_vals = values.get("name", [])
            name = name_vals[0].get("value", "") if name_vals else ""

            domain_vals = values.get("domains", [])
            domain = domain_vals[0].get("domain", "") if domain_vals else ""

            desc_vals = values.get("description", [])
            description = desc_vals[0].get("value", "") if desc_vals else ""

            lookup[rid] = {"name": name, "domain": domain, "description": description}

        print(".", end="", flush=True)
        # keep paginating as long as we received a full batch
        if len(batch) < page_size:
            break
        offset += len(batch)

    print(f" {len(lookup)} companies loaded.")
    return lookup


def get_entry_attr(entry, key):
    """Extract a scalar value from a list entry's entry_values."""
    values = entry.get("entry_values", {}).get(key, [])
    if not values:
        return None
    v = values[0]
    if "option" in v:
        return v["option"]["title"]
    if "status" in v:
        return v["status"]["title"]
    if "value" in v:
        return v["value"]
    if "currency_value" in v:
        return v["currency_value"]
    return None


def update_list_entry(entry_id, updates, dry_run=False):
    if dry_run:
        print(f"    [dry-run] {entry_id}: {list(updates.keys())}")
        return
    r = requests.patch(
        f"{ATTIO_BASE}/lists/{WATCHLIST_ID}/entries/{entry_id}",
        headers=attio_headers(),
        json={"data": {"entry_values": updates}},
    )
    if not r.ok:
        print(f"    [warn] Update failed ({r.status_code})")
        print(f"    payload: {updates}")
        print(f"    response: {r.text[:400]}")
    r.raise_for_status()


# ─── Web search ──────────────────────────────────────────────────────────────

def serper(query, num=8):
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        return []
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": num},
    )
    if not r.ok:
        return []
    return r.json().get("organic", [])


def snippets_text(results):
    return " ".join(r.get("snippet", "") + " " + r.get("title", "") for r in results)


def _search_queries(name, domain):
    """Return two queries: domain-anchored (more specific) and name-only fallback."""
    if domain:
        primary = f'site:{domain} OR "{name}" {domain}'
    else:
        primary = f'"{name}" startup'
    fallback = f'"{name}" startup'
    return primary, fallback


def find_sector(name, description, domain):
    primary, fallback = _search_queries(name, domain)
    results = serper(f'{primary} industry sector funding')
    if not results:
        results = serper(f'{fallback} industry sector')
    text = snippets_text(results).lower() + " " + description.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return sector
    return None


def find_funding(name, domain):
    primary, fallback = _search_queries(name, domain)
    results = serper(f'{primary} funding round raised million crunchbase pitchbook')
    if not results:
        results = serper(f'{fallback} funding round raised')
    text = snippets_text(results)

    round_pat = re.compile(r"\b(Pre-?[Ss]eed|[Ss]eed|[Ss]eries\s+[A-E])\b")
    amount_pat = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(M|B|million|billion)", re.IGNORECASE)

    rounds, amounts = [], []
    for m in round_pat.finditer(text):
        rounds.append(m.group(0).strip())
    for m in amount_pat.finditer(text):
        val = float(m.group(1))
        if m.group(2).lower() in ("b", "billion"):
            val *= 1000
        amounts.append(val * 1_000_000)

    round_order = ["Series E", "Series D", "Series C", "Series B", "Series A", "Seed", "Pre-seed"]
    best_round = next((ro for ro in round_order for fr in rounds if ro.lower() in fr.lower()), None)
    best_amount = amounts[0] if amounts else None

    return best_round, best_amount


def find_investors(name, domain):
    primary, fallback = _search_queries(name, domain)
    results = serper(f'{primary} investors venture capital backed by')
    if not results:
        results = serper(f'{fallback} investors backed by')
    text = snippets_text(results)
    patterns = [
        r"(?:backed by|investors include|led by|investors:?)\s+([^.]{10,150})",
        r"([A-Z][A-Za-z ]+(?:Capital|Ventures|Partners|Fund|VC|Invest)[^,\n]{0,40}"
        r"(?:,\s*[A-Z][A-Za-z ]+(?:Capital|Ventures|Partners|Fund|VC|Invest)[^,\n]{0,40})*)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(1).strip().rstrip(".,")
            if len(raw) > 10:
                return raw
    return None


# ─── Slack search ────────────────────────────────────────────────────────────

def slack_search(name):
    token = os.environ.get("SLACK_TOKEN", "")
    if not token:
        return None
    channels = ["companies", "deals", "news-and-readings"]
    mentions = []
    for ch in channels:
        r = requests.get(
            SLACK_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"query": f"{name} in:#{ch}", "count": 3},
        )
        if not r.ok:
            continue
        for m in r.json().get("messages", {}).get("matches", []):
            text = m.get("text", "")[:200]
            user = m.get("username", "unknown")
            try:
                date_str = datetime.fromtimestamp(float(m.get("ts", 0))).strftime("%Y-%m-%d")
            except Exception:
                date_str = ""
            mentions.append(f'[Slack #{ch}]: "{text}" - @{user} {date_str}')
    return "\n".join(mentions) if mentions else None


# ─── State ───────────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()).get("processed", []))
    return set()


def save_state(processed):
    STATE_FILE.write_text(json.dumps({"processed": list(processed)}, indent=2))


# ─── Enrichment ──────────────────────────────────────────────────────────────

def is_garbage(value):
    """Return True if a field value looks like raw JSON/search results dumped by mistake."""
    if not isinstance(value, str):
        return False
    return value.strip().startswith("{") and "results" in value


def needs_enrichment(entry):
    sector    = get_entry_attr(entry, "sector")
    rnd       = get_entry_attr(entry, "funding_round")
    investors = get_entry_attr(entry, "current_investors")
    amount    = get_entry_attr(entry, "amount_invested")
    return (
        sector is None
        or rnd is None
        or investors is None or is_garbage(investors)
        or amount is None
    )


def enrich(entry, company, dry_run=False):
    entry_id    = entry["id"]["entry_id"]
    name        = company.get("name", "").strip()
    domain      = company.get("domain", "").strip()
    description = company.get("description", "").strip()

    # Use domain as search key if name is missing
    search_name = name or domain.split(".")[0].replace("-", " ").title()
    if not search_name:
        return {"name": entry["parent_record_id"][:8], "status": "✗ No name or domain"}

    display = name if name else f"{domain} (no name)"
    print(f"\n  → {display}")

    updates, sources = {}, []
    row = {
        "name": display,
        "sector": get_entry_attr(entry, "sector"),
        "funding_round": get_entry_attr(entry, "funding_round"),
        "funding_raised": get_entry_attr(entry, "amount_invested"),
        "investors": get_entry_attr(entry, "current_investors"),
        "slack": "✗",
        "status": "✓ Updated",
    }

    # Sector
    if row["sector"] is None:
        s = find_sector(search_name, description, domain)
        if s:
            updates["sector"] = s
            row["sector"] = s
            sources.append("web")

    # Funding round + amount
    if row["funding_round"] is None or row["funding_raised"] is None:
        rnd, amt = find_funding(search_name, domain)
        if rnd and row["funding_round"] is None:
            updates["funding_round"] = rnd
            row["funding_round"] = rnd
            sources.append("Crunchbase/web")
        if amt and row["funding_raised"] is None:
            updates["amount_invested"] = amt
            row["funding_raised"] = f"${amt/1e6:.0f}M"

    # Investors
    if row["investors"] is None:
        inv = find_investors(search_name, domain)
        if inv:
            updates["current_investors"] = inv
            row["investors"] = inv

    # Slack
    slack_note = slack_search(search_name)
    if slack_note:
        existing = get_entry_attr(entry, "investor_comment") or ""
        updates["investor_comment"] = (existing + "\n" + slack_note).strip()
        row["slack"] = "✓"
        sources.append("Slack")

    # Ensure deal_stage set
    if get_entry_attr(entry, "deal_stage") is None:
        updates["deal_stage"] = "Watchlist"

    row["sources"] = ", ".join(sources) if sources else "—"

    if not updates:
        row["status"] = "— No new data found"
    else:
        try:
            update_list_entry(entry_id, updates, dry_run=dry_run)
        except Exception as e:
            row["status"] = f"✗ Error: {e}"

    time.sleep(1)
    return row


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",      action="store_true", help="Re-enrich all entries")
    parser.add_argument("--new-only", action="store_true", help="Only unprocessed entries")
    parser.add_argument("--dry-run",  action="store_true", help="Preview without writing")
    parser.add_argument("--company",  nargs="+", help="Force enrich specific company names (e.g. --company Ampere Aperture)")
    args = parser.parse_args()

    # Load .env
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    processed = load_state()

    print("Fetching Attio watchlist…")
    entries = get_watchlist_entries()
    print(f"  Found {len(entries)} entries")

    company_lookup = build_company_lookup()
    if not company_lookup:
        print("Cannot proceed without company names. Check API key scopes.")
        return

    force_names = [n.lower() for n in (args.company or [])]

    to_process = []
    for entry in entries:
        record_id = entry["parent_record_id"]
        company   = company_lookup.get(record_id, {})
        name      = company.get("name", "").lower()

        if force_names:
            if any(fn in name for fn in force_names):
                to_process.append(entry)
            continue

        if args.new_only and record_id in processed:
            continue
        if not args.all and not needs_enrichment(entry):
            continue
        to_process.append(entry)

    print(f"  {len(to_process)} entries to enrich")

    rows = []
    for entry in to_process:
        record_id = entry["parent_record_id"]
        company = company_lookup.get(record_id, {"name": f"[{record_id[:8]}]", "domain": "", "description": ""})
        try:
            row = enrich(entry, company, dry_run=args.dry_run)
            rows.append(row)
            processed.add(record_id)
            if not args.dry_run:
                save_state(processed)
        except Exception as e:
            print(f"  ✗ Error: {e}")
            rows.append({"name": company.get("name", record_id), "status": f"✗ {e}"})

    # Summary table
    W = 100
    print("\n" + "=" * W)
    print(f"{'Company':<28} {'Sector':<16} {'Round':<10} {'Amount':<10} {'Investors':<22} {'Sl':<4} {'Sources':<20} Status")
    print("-" * W)
    for r in rows:
        print(
            f"{str(r.get('name',''))[:27]:<28}"
            f"{str(r.get('sector','—'))[:15]:<16}"
            f"{str(r.get('funding_round','—'))[:9]:<10}"
            f"{str(r.get('funding_raised','—'))[:9]:<10}"
            f"{str(r.get('investors','—'))[:21]:<22}"
            f"{r.get('slack','✗'):<4}"
            f"{str(r.get('sources',''))[:19]:<20}"
            f"{r.get('status','')}"
        )
    print("=" * W)
    updated = len([r for r in rows if "✓ Updated" in r.get("status", "")])
    errors  = len([r for r in rows if "✗" in r.get("status", "")])
    print(f"\nDone — {updated} updated, {errors} errors, {len(rows)-updated-errors} no new data.")


if __name__ == "__main__":
    main()
