# Jim — Attio Watchlist Enricher

Automatically enriches Outline Ventures' Attio watchlist with sector, funding round, investors, and Slack commentary.

## Setup

### 1. Install dependencies
```bash
pip install requests
```

### 2. Create `.env` from template
```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

**Required keys:**
| Key | Where to get it |
|-----|----------------|
| `ATTIO_API_KEY` | Attio → Settings → API → Create key |
| `SERPER_API_KEY` | [serper.dev](https://serper.dev) — free tier: 2,500 searches/mo |
| `SLACK_TOKEN` | Slack App OAuth token (optional, for Slack commentary) |

### 3. Run locally
```bash
# Enrich only entries missing sector or investors (default)
python jim_enricher.py

# Re-enrich all entries
python jim_enricher.py --all

# Only process entries never seen before (new entry detection)
python jim_enricher.py --new-only

# Preview planned changes without writing to Attio
python jim_enricher.py --dry-run
```

## GitHub Actions (automated)

1. Push this repo to GitHub
2. Add secrets: `ATTIO_API_KEY`, `SERPER_API_KEY`, `SLACK_TOKEN`  
   → Settings → Secrets and variables → Actions → New repository secret
3. The workflow runs **every hour** automatically
4. You can also trigger it manually from the Actions tab and choose a run mode

## What gets updated

| Field | Source |
|-------|--------|
| Sector | Web search → matched to existing Attio sector labels |
| Funding round | Crunchbase / TechCrunch snippets |
| Current investors | Web search snippets |
| Investor comment | Slack #companies, #deals, #news-and-readings |
| Deal stage | Set to "Watchlist" if blank |
