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
    "Agriculture", "Life Sciences", "Healthcare", "Consumer & Retail",
    "Mining", "Warehousing", "Construction", "Automotive",
    "Supply Chain & Logistics", "Manufacturing", "Circularity",
    "Packaging & Material", "Food & Restaurant",
]

OUTLINE_THESIS = """
Outline Ventures is a Physical AI fund (formerly Rethink Food, led by Rini Greenfield).
We back B2B, asset-light companies that apply AI, automation, and data to modernize
incumbent-led physical-world systems.

Winning pattern: platforms that turn fragmented, offline, or human-driven systems into
data-driven operating systems — positioned to become incumbents' fastest path to modernization.

Rules:
- Delivery model: software, software + hardware, or data — but must be asset-light (no capex-heavy operators)
- Scale partner, not tech incubator — we do NOT take technical risk
- Engage at early commercialization (tech validated, starting to sell)
- Must have measurable real-world benefit — not pure software, not AI-for-AI's-sake
- Stage: Seed (surface & track toward Series A), small/early Series A (primary investment stage),
  select Series B (market-intel only). Pre-seed = flag as "Early" only. Series C+ = exclude.
- Must be B2B (not B2C consumer products)
"""

# Keywords that signal a company likely does NOT fit the thesis
ANTI_THESIS_SIGNALS = [
    "b2c", "direct to consumer", "d2c", "consumer app", "social media",
    "pure software", "no physical", "fintech", "insurtech", "edtech",
    "series c", "series d", "series e", "late stage",
    "capex heavy", "asset heavy", "real estate",
]

# Keywords that signal strong thesis alignment
PRO_THESIS_SIGNALS = [
    "physical ai", "robotics", "automation", "b2b", "saas", "supply chain",
    "logistics", "agriculture", "manufacturing", "construction", "fleet",
    "warehousing", "food tech", "industrial", "ai-powered", "data platform",
    "operating system", "incumbent", "modernize",
]


def thesis_alignment_score(name, description, sector, search_text):
    """
    Returns (score, flag) where score > 0 means aligned, < 0 means misaligned.
    flag is one of: 'Aligned', 'Weak fit', 'Likely misaligned', or None.
    """
    combined = (description + " " + search_text + " " + (sector or "")).lower()
    pro = sum(1 for kw in PRO_THESIS_SIGNALS if kw in combined)
    anti = sum(1 for kw in ANTI_THESIS_SIGNALS if kw in combined)
    score = pro - (anti * 2)
    if score >= 2:
        return score, "Aligned"
    elif score >= 0:
        return score, "Weak fit"
    else:
        return score, "Likely misaligned"


SECTOR_KEYWORDS = {
    "Agriculture":            ["agri", "farm", "crop", "soil", "precision ag", "livestock", "aquaculture", "horticulture"],
    "Life Sciences":          ["biotech", "genomic", "life science", "synthetic bio", "crispr", "drug discovery", "biopharma", "clinical", "lab automation"],
    "Healthcare":             ["healthcare", "health tech", "medical device", "hospital", "patient", "clinical workflow", "diagnostics", "elder care"],
    "Consumer & Retail":      ["retail", "consumer brand", "direct to consumer", "d2c", "e-commerce", "point of sale", "pos ", "wholesale", "distributor", "b2b marketplace", "retail tech"],
    "Mining":                 ["mining", "mineral extraction", "quarry", "drilling", "ore"],
    "Warehousing":            ["warehousing", "warehouse", "fulfillment center", "dark store", "pick and pack"],
    "Construction":           ["construction", "building", "concrete", "rebar", "contractor", "jobsite", "modular build"],
    "Automotive":             ["automotive", "vehicle", "ev ", "electric vehicle", "fleet", "telematics", "fleet management", "fleet tracking", "micromobility", "e-scooter", "e-bike"],
    "Supply Chain & Logistics": ["supply chain", "procurement", "sourcing", "inventory", "logistics", "shipping", "freight", "last mile", "delivery", "carrier", "3pl"],
    "Manufacturing":          ["manufactur", "factory", "industrial", "fabricat", "production", "robotics", "cobots", "cnc", "quality control"],
    "Circularity":            ["circular economy", "recycling", "upcycling", "waste reduction", "reverse logistics", "sustainability"],
    "Packaging & Material":   ["packaging", "materials", "metals", "steel", "aluminum", "composites", "paper", "corrugated", "label", "container"],
    "Food & Restaurant":      ["restaurant", "foodservice", "food tech", "ghost kitchen", "catering", "food manufacturing", "food safety", "food distribution"],
}


# ─── Attio helpers ───────────────────────────────────────────────────────────

def attio_headers():
    key = os.environ.get("ATTIO_API_KEY", "")
    if not key:
        raise ValueError("ATTIO_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def find_people_at_funds(investor_names):
    """
    Search Attio People records for contacts whose current company matches
    any of the investor fund names. Returns a list of record IDs for the
    potential_investor_contact Record field.
    """
    if not investor_names:
        return []

    found = []
    for fund in investor_names:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records/query",
            headers=attio_headers(),
            json={
                "filter": {
                    "company": {"$contains": fund}
                },
                "limit": 10,
            },
        )
        if not r.ok:
            continue
        for rec in r.json().get("data", []):
            record_id = rec.get("id", {}).get("record_id")
            if record_id:
                found.append({"target_record_id": record_id})

    # Deduplicate by record_id
    seen, deduped = set(), []
    for item in found:
        rid = item["target_record_id"]
        if rid not in seen:
            seen.add(rid)
            deduped.append(item)
    return deduped


def list_watchlist_fields():
    """Print all field slugs on the watchlist by inspecting a live entry."""
    entries = get_watchlist_entries()
    if not entries:
        print("No entries found in watchlist.")
        return
    sample = entries[0].get("entry_values", {})
    print(f"\n{'Slug':<40} Sample value")
    print("-" * 80)
    for slug, vals in sorted(sample.items()):
        sample_val = vals[0] if vals else {}
        print(f"{slug:<40} {str(sample_val)[:60]}")
    print()


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

            type_vals = values.get("record_type", [])
            record_type = type_vals[0].get("option", {}).get("title", "") if type_vals else ""

            lookup[rid] = {"name": name, "domain": domain, "description": description, "record_type": record_type}

        print(".", end="", flush=True)
        # keep paginating as long as we received a full batch
        if len(batch) < page_size:
            break
        offset += len(batch)

    print(f" {len(lookup)} companies loaded.")
    return lookup


def set_company_record_type(record_id, dry_run=False):
    """Set record_type = 'Company' on the company object if it's blank."""
    if dry_run:
        print(f"    [dry-run] set record_type=Company on {record_id[:8]}")
        return
    r = requests.patch(
        f"{ATTIO_BASE}/objects/companies/records/{record_id}",
        headers=attio_headers(),
        json={"data": {"values": {"record_type": "Company"}}},
    )
    if not r.ok:
        print(f"    [warn] Could not set record_type ({r.status_code}): {r.text[:120]}")


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


def _result_thesis_score(result):
    """Score a single search result snippet against the thesis — used to disambiguate generic names."""
    text = (result.get("snippet", "") + " " + result.get("title", "")).lower()
    pro  = sum(1 for kw in PRO_THESIS_SIGNALS  if kw in text)
    anti = sum(1 for kw in ANTI_THESIS_SIGNALS if kw in text)
    return pro - (anti * 2)


def rank_results_by_thesis(results):
    """
    Re-order search results so thesis-aligned snippets come first.
    For generic company names (e.g. 'Apphere') this ensures we extract
    sector/funding/investors from the most relevant match, not a
    coincidentally named CPG or consumer brand.
    """
    return sorted(results, key=_result_thesis_score, reverse=True)


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
    results = rank_results_by_thesis(results)
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
    results = rank_results_by_thesis(results)
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


def find_last_round_date(name, domain):
    """Search recent news for when the last funding round was raised. Returns a date string or None."""
    primary, fallback = _search_queries(name, domain)
    results = serper(f'{primary} funding round raised 2023 2024 2025', num=10)
    if not results:
        results = serper(f'{fallback} funding round raised', num=10)
    results = rank_results_by_thesis(results)

    date_patterns = [
        # "raised $10M in January 2024" / "closed a $5M round in March 2025"
        r"(?:raised|closed|announced|secured)[^.]{0,60}?\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})",
        # "2024-03-15" or "03/15/2024"
        r"\b(20\d{2})[.\-/](0[1-9]|1[0-2])[.\-/](0[1-9]|[12]\d|3[01])\b",
        # "Q1 2024"
        r"\b(Q[1-4]\s+20\d{2})\b",
        # standalone year near funding keyword — last resort
        r"(?:raised|funded|round)[^.]{0,40}?\b(20(?:2[0-9]))\b",
    ]

    # Check each result's snippet + date field individually, prefer results with explicit dates
    best_date = None
    for result in results:
        snippet = result.get("snippet", "") + " " + result.get("title", "")
        # Serper sometimes returns a `date` field on news results
        if result.get("date"):
            try:
                d = datetime.strptime(result["date"], "%b %d, %Y")
                candidate = d.strftime("%Y-%m-%d")
                if best_date is None or candidate > best_date:
                    best_date = candidate
                continue
            except Exception:
                pass
        for pat in date_patterns:
            m = re.search(pat, snippet, re.IGNORECASE)
            if m:
                groups = m.groups()
                # Month + Year format
                if len(groups) == 2 and not groups[0].startswith("Q") and not groups[0].startswith("20"):
                    try:
                        d = datetime.strptime(f"{groups[0]} {groups[1]}", "%B %Y")
                        candidate = d.strftime("%Y-%m")
                        if best_date is None or candidate > best_date:
                            best_date = candidate
                    except Exception:
                        pass
                # Full ISO date
                elif len(groups) == 3:
                    candidate = f"{groups[0]}-{groups[1]}-{groups[2]}"
                    if best_date is None or candidate > best_date:
                        best_date = candidate
                # Q1 2024 or standalone year
                else:
                    candidate = groups[0]
                    if best_date is None or candidate > best_date:
                        best_date = candidate
                break
    return best_date


# Noise tokens — rejected immediately, no search needed
INVESTOR_BLOCKLIST = {
    "the", "and", "with", "from", "including", "also", "led", "backed", "by",
    "other", "several", "existing", "new", "strategic", "notable", "among",
    "according", "announced", "raised", "million", "billion", "round", "series",
    "seed", "pre", "post", "late", "stage", "funding", "investors", "investor",
    "participating", "participated", "participation", "co-invested", "co-investing",
    "alongside", "joining", "joined", "alongside", "who", "have", "invested", "in",
}

# Suffixes that confirm a token is a fund — accepted immediately, no search needed
VC_SUFFIXES = [
    "capital", "ventures", "venture", "partners", "fund", "equity", "invest",
    " vc", "growth", "holdings", "asset management", "innovations",
    "accelerator", "incubator", "angel", "syndicate", "family office",
]

# Well-known fund names without standard suffixes — accepted immediately, no search needed
KNOWN_FUNDS = {
    "y combinator", "a16z", "techstars", "antler", "sequoia", "accel",
    "bessemer", "lightspeed", "greylock", "khosla", "founders fund",
    "500 startups", "plug and play", "dcvc", "gv", "nea", "ifc",
    "softbank", "tiger global", "coatue", "insight partners",
    "lux capital", "lux", "general catalyst", "first round", "index",
    "spark capital", "union square", "usv", "benchmark", "felicis",
    "initialized", "flatiron", "lowercase", "collaborative fund",
}

# Keywords in search snippets that confirm an ambiguous name is a fund
FUND_CONFIRM_KEYWORDS = [
    "venture capital", "vc firm", "investment firm", "private equity",
    "venture fund", "seed fund", "growth fund", "asset management",
    "portfolio companies", "lead investor", "co-investor",
    "accelerator", "incubator", "angel fund", "family office",
]

_fund_cache = {}  # cache per run to avoid duplicate searches


def is_verified_fund(name):
    """
    Three-tier check to minimise Serper usage:
    1. Blocklist / length / noise — reject immediately (free)
    2. Suffix at end of name / known-names — accept immediately (free)
    3. Ambiguous short names — one web search, cached per run
    """
    name = name.strip()
    lower = name.lower()

    # Reject if too long (sentences, not fund names) or too short
    if len(name) > 45 or len(name) < 3:
        return False
    # Reject if any word in the token is a clear noise word
    words = lower.split()
    if any(w in INVESTOR_BLOCKLIST for w in words):
        return False
    # Reject if contains digits mixed with non-fund words (e.g. "7M", "000", "$3.4")
    if re.search(r'\$|\bM\b|\bB\b|million|billion|%', name, re.IGNORECASE):
        return False
    # Reject person-name patterns (single word or "Firstname Lastname" with no fund suffix)
    # A fund name almost always has 2+ words
    if len(words) == 1 and lower not in KNOWN_FUNDS:
        return False

    # Accept if suffix appears anywhere in the name
    if any(s in lower for s in VC_SUFFIXES) or lower in KNOWN_FUNDS:
        return True

    # Ambiguous — accept by default, only reject if search confirms it's NOT a fund
    if lower in _fund_cache:
        return _fund_cache[lower]
    results = serper(f'"{name}" venture capital fund investor', num=3)
    text = snippets_text(results).lower()
    NOT_FUND_SIGNALS = ["product", "software company", "consumer brand", "restaurant", "retailer"]
    is_not_fund = not any(kw in text for kw in FUND_CONFIRM_KEYWORDS) and any(s in text for s in NOT_FUND_SIGNALS)
    _fund_cache[lower] = not is_not_fund
    return not is_not_fund


def normalize_investors(raw):
    """
    Clean a raw investor string into "Firm A, Firm B, Firm C".
    Splits on commas/semicolons/'and'/newlines, then runs each token through
    is_verified_fund() which uses free checks first and only searches ambiguous names.
    Returns None if nothing valid remains.
    """
    if not raw:
        return None

    raw = re.sub(r'\s+and\s+', ', ', raw, flags=re.IGNORECASE)
    raw = re.sub(r'[;\n·•|/]', ',', raw)  # handle bullet separators from Tracxn, Crunchbase etc.
    tokens = [t.strip().strip('.,') for t in raw.split(',')]

    valid = []
    for token in tokens:
        token = token.strip()
        if not token or len(token) < 3 or len(token) > 60:
            continue
        # Allow digits only if token also contains letters (e.g. "1517 Fund", "8090 Industries")
        if re.search(r'\d', token) and not re.search(r'[A-Za-z]', token):
            continue
        if is_verified_fund(token):
            valid.append(token)

    if not valid:
        return None

    seen, deduped = set(), []
    for v in valid:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(v)
    return ", ".join(deduped)


def find_investors(name, domain):
    primary, fallback = _search_queries(name, domain)
    # Search 1: aggregator sites (Crunchbase, Pitchbook style)
    results = serper(f'{primary} investors venture capital backed by')
    if not results:
        results = serper(f'{fallback} investors backed by')
    # Search 2: TechCrunch — best source for "participating" / "co-investing" phrasing
    tc_results = serper(f'"{name}" funding site:techcrunch.com', num=5)
    # Search 3: press releases — BusinessWire / PRNewswire use formal investor lists
    pr_results = serper(f'"{name}" funding round announces site:businesswire.com OR site:prnewswire.com', num=3)
    results = rank_results_by_thesis(results + tc_results + pr_results)
    text = snippets_text(results)
    INVESTOR_TRIGGERS = (
        r"backed by|investors include|led by|investors:|co-invested with|co-investing with|"
        r"participating were|also participating|participation from|participants include|"
        r"participated in(?:\s+the round)?|participating in(?:\s+the round)?|"
        r"joined by|joining the round|joining in|"
        r"with participation from|with co-investment from|alongside"
    )
    # Bullet-separated fragments (·, •) — merge all into one candidate
    bullet_pat = r'([A-Za-z0-9][A-Za-z0-9 ]+(?:\s*[·•]\s*[A-Za-z0-9][A-Za-z0-9 ]+){1,})'
    bullet_matches = [m.group(1).strip() for m in re.finditer(bullet_pat, text)]
    bullet_combined = ", ".join(bullet_matches) if bullet_matches else ""

    patterns = [
        # "Firm A, Firm B, and Firm C are N of N investors who have invested in"
        r"([A-Za-z0-9][^.?!]{10,300}?)\s+are\s+\d+\s+of\s+\d+\s+investors",
        # Trigger phrase followed by investor list up to end of sentence
        rf"(?:{INVESTOR_TRIGGERS})\s+([^.{{}}]{{10,300}})",
        # Explicit fund name chains
        r"([A-Z][A-Za-z ]+(?:Capital|Ventures|Partners|Fund|VC|Invest)[^,\n]{{0,40}}"
        r"(?:,\s*[A-Z][A-Za-z ]+(?:Capital|Ventures|Partners|Fund|VC|Invest)[^,\n]{{0,40}})*)",
    ]
    candidates = [bullet_combined] if bullet_combined else []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = m.group(1).strip().rstrip(".,")
            if len(raw) > 10:
                candidates.append(raw)
    if not candidates:
        return None
    # Normalize each candidate and pick the one with the most valid fund names
    best_result, best_count = None, 0
    for raw in candidates:
        normalized = normalize_investors(raw)
        if normalized:
            count = len(normalized.split(','))
            if count > best_count:
                best_count = count
                best_result = normalized
    return best_result


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


# Maps old sector labels → new ones for instant migration without a web search
SECTOR_REMAP = {
    "BioTech":           "Life Sciences",
    "Design":            None,                   # no clean mapping — re-search
    "Enterprise":        None,                   # no clean mapping — re-search
    "SaaS":              None,                   # no clean mapping — re-search
    "Retail":            "Consumer & Retail",
    "Retail tech":       "Consumer & Retail",
    "Wholesale":         "Consumer & Retail",
    "Physical AI":       None,                   # no clean mapping — re-search
    "Fleet":             "Automotive",
    "Micromobility":     "Automotive",
    "Consumer hardware": None,                   # no clean mapping — re-search
    "Logistics":         "Supply Chain & Logistics",
    "Supply Chain":      "Supply Chain & Logistics",
    "Materials":         "Packaging & Material",
    "Restaurant":        "Food & Restaurant",
}

VALID_SECTORS = set(SECTOR_OPTIONS)


def sector_needs_update(sector):
    """Return True if the sector is an old label or not in the current valid set."""
    if sector is None:
        return True
    if sector in SECTOR_REMAP:
        return True
    return sector not in VALID_SECTORS


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
        sector_needs_update(sector)
        or rnd is None
        or investors is None or is_garbage(investors)
        or amount is None
    )


def enrich(entry, company, dry_run=False, update_investors=False):
    entry_id    = entry["id"]["entry_id"]
    record_id   = entry["parent_record_id"]
    name        = company.get("name", "").strip()
    domain      = company.get("domain", "").strip()
    description = company.get("description", "").strip()

    # Set record_type = Company on the company object if blank
    if not company.get("record_type"):
        set_company_record_type(record_id, dry_run=dry_run)

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
        "thesis_fit": "—",
        "status": "✓ Updated",
    }

    # Sector — migrate old labels or re-search if stale/missing
    search_text = ""
    if sector_needs_update(row["sector"]):
        old_sector = row["sector"]
        remapped = SECTOR_REMAP.get(old_sector)  # None means either unknown or needs re-search
        if remapped:
            updates["sector"] = remapped
            row["sector"] = remapped
            sources.append("remap")
            if old_sector:
                print(f"    sector: '{old_sector}' → '{remapped}' (remapped)")
        else:
            s = find_sector(search_name, description, domain)
            if s:
                updates["sector"] = s
                row["sector"] = s
                sources.append("web")
                if old_sector:
                    print(f"    sector: '{old_sector}' → '{s}' (re-searched)")
            else:
                updates["sector"] = None
                row["sector"] = None
                sources.append("cleared")
                if old_sector:
                    print(f"    sector: '{old_sector}' → blank (no match found)")

    # Thesis alignment — run after sector is resolved, using search snippets
    primary, _ = _search_queries(search_name, domain)
    search_results = serper(f'{primary} B2B automation AI physical-world')
    search_text = snippets_text(search_results)
    _, thesis_flag = thesis_alignment_score(search_name, description, row["sector"], search_text)
    row["thesis_fit"] = thesis_flag
    # thesis_fit is display-only — no matching Attio field

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

    # Last round date — always refresh from news
    last_round_date = find_last_round_date(search_name, domain)
    row["last_round_raised_time"] = last_round_date or "—"
    if last_round_date:
        updates["last_round_raised_time"] = last_round_date

    # May raise soon flag
    # Thresholds based on industry average time between rounds:
    # Seed → Series A: ~15 months; Series A → B and later: ~24 months
    # Source: https://ff.co/startup-statistics-guide/
    row["may_raise_soon"] = ""
    if last_round_date and row.get("funding_round"):
        try:
            # Parse either YYYY-MM or YYYY-MM-DD
            fmt = "%Y-%m-%d" if len(last_round_date) > 7 else "%Y-%m"
            raised_dt = datetime.strptime(last_round_date, fmt)
            months_since = (datetime.now() - raised_dt).days / 30.44
            stage = row["funding_round"].lower()
            is_seed_or_earlier = any(s in stage for s in ("seed", "pre-seed", "preseed"))
            threshold = 15 if is_seed_or_earlier else 24
            if months_since >= threshold:
                updates["fundraising_flag"] = "May Raise Soon"
                row["may_raise_soon"] = "⚑ May Raise Soon"
        except Exception:
            pass

    # Investors — re-search if missing or if --update-investors flag is set
    existing_inv = row["investors"]
    if existing_inv and not update_investors:
        cleaned = normalize_investors(existing_inv)
        if cleaned and cleaned != existing_inv:
            updates["current_investors"] = cleaned
            row["investors"] = cleaned
    else:
        inv = find_investors(search_name, domain)
        if inv:
            updates["current_investors"] = inv
            row["investors"] = inv
        elif update_investors:
            # Clear stale/garbage data rather than leaving old bad values
            updates["current_investors"] = ""
            row["investors"] = None

    # Potential investor connections — search Attio People for contacts at known funds
    final_investors = row.get("investors") or ""
    fund_names = [f.strip() for f in final_investors.split(",") if f.strip()]
    connections = find_people_at_funds(fund_names)
    row["connections"] = f"{len(connections)} contact(s)" if connections else "—"
    if connections:
        updates["potential_investor_contact"] = connections

    # Slack
    slack_note = slack_search(search_name)
    if slack_note:
        existing = get_entry_attr(entry, "investor_comment") or ""
        updates["investor_comment"] = (existing + "\n" + slack_note).strip()
        row["slack"] = "✓"
        sources.append("Slack")

    # Fill deal_stage only if blank
    if get_entry_attr(entry, "deal_stage") is None:
        updates["deal_stage"] = "Watchlist"

    row["sources"] = ", ".join(sources) if sources else "—"

    if not updates:
        row["status"] = "— No new data found"
    else:
        try:
            update_list_entry(entry_id, updates, dry_run=dry_run)
        except Exception:
            # Batch failed — retry each field individually to isolate the bad one
            failed_fields = []
            for field, value in updates.items():
                try:
                    update_list_entry(entry_id, {field: value}, dry_run=dry_run)
                except Exception as e2:
                    failed_fields.append(f"{field}({e2})")
            if failed_fields:
                row["status"] = f"✗ Failed fields: {', '.join(failed_fields)}"

    time.sleep(1)
    return row


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",            action="store_true", help="Re-enrich all entries")
    parser.add_argument("--new-only",       action="store_true", help="Only unprocessed entries")
    parser.add_argument("--dry-run",        action="store_true", help="Preview without writing")
    parser.add_argument("--update-sectors", action="store_true", help="Re-evaluate sector for all entries (migrates old labels, re-searches unmappable ones)")
    parser.add_argument("--list-fields",       action="store_true", help="Print all Attio watchlist field slugs and exit")
    parser.add_argument("--update-investors", action="store_true", help="Re-search and overwrite investor field for all entries")
    parser.add_argument("--company",        nargs="+", help="Force enrich specific company names (e.g. --company Ampere Aperture)")
    args = parser.parse_args()

    # Load .env
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    if args.list_fields:
        list_watchlist_fields()
        return

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
        if args.update_sectors:
            if sector_needs_update(get_entry_attr(entry, "sector")):
                to_process.append(entry)
            continue
        if args.update_investors:
            to_process.append(entry)
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
            row = enrich(entry, company, dry_run=args.dry_run, update_investors=args.update_investors)
            rows.append(row)
            processed.add(record_id)
            if not args.dry_run:
                save_state(processed)
        except Exception as e:
            print(f"  ✗ Error: {e}")
            rows.append({"name": company.get("name", record_id), "status": f"✗ {e}"})

    # Summary table
    W = 148
    print("\n" + "=" * W)
    print(f"{'Company':<28} {'Sector':<16} {'Round':<10} {'Amount':<10} {'Last Round':<12} {'Raise Flag':<18} {'Connections':<14} {'Investors':<16} {'Thesis Fit':<16} {'Sl':<4} {'Sources':<12} Status")
    print("-" * W)
    for r in rows:
        print(
            f"{str(r.get('name',''))[:27]:<28}"
            f"{str(r.get('sector','—'))[:15]:<16}"
            f"{str(r.get('funding_round','—'))[:9]:<10}"
            f"{str(r.get('funding_raised','—'))[:9]:<10}"
            f"{str(r.get('last_round_raised_time','—'))[:11]:<12}"
            f"{str(r.get('may_raise_soon',''))[:17]:<18}"
            f"{str(r.get('connections','—'))[:13]:<14}"
            f"{str(r.get('investors','—'))[:15]:<16}"
            f"{str(r.get('thesis_fit','—'))[:15]:<16}"
            f"{r.get('slack','✗'):<4}"
            f"{str(r.get('sources',''))[:11]:<12}"
            f"{r.get('status','')}"
        )
    print("=" * W)
    updated = len([r for r in rows if "✓ Updated" in r.get("status", "")])
    errors  = len([r for r in rows if "✗" in r.get("status", "")])
    print(f"\nDone — {updated} updated, {errors} errors, {len(rows)-updated-errors} no new data.")


if __name__ == "__main__":
    main()
