# Changelog

## 2026-06-26

1. Added Outline Ventures' full investment thesis as a constant in the script.
2. Added thesis alignment scoring to flag each company as Aligned, Weak fit, or Likely misaligned.
3. Search results are now ranked by thesis alignment before extracting data, so generic company names resolve to the most thesis-relevant match.
4. Replaced 22 sector labels with 13 Outline-aligned categories.
5. Added a sector migration map so old labels are instantly remapped without a web search.
6. Companies with unmappable old sectors are re-searched and cleared to blank if no match is found.
7. Added `--update-sectors` flag to re-evaluate sector for all entries in one sweep.
8. Added `find_last_round_date()` to search recent news for when each company last raised, always refreshing on every run.
9. Added a "May Raise Soon" flag based on industry-average time between rounds (15 months for Seed, 24 months for Series A+).
10. Added `fundraising_flag` and `last_round_raised_time` fields wired to Attio.
11. Added `normalize_investors()` to clean investor strings into a consistent comma-separated list of verified fund names.
12. Added three-tier fund verification: blocklist rejection, suffix/known-name acceptance, and web search fallback for ambiguous names.
13. Investor search now pulls from aggregator sites, TechCrunch, and press releases to catch "participating" and "co-investing" phrasing.
14. Bullet-separated investor lists (·, •) are now parsed correctly.
15. Added `--update-investors` flag to force re-search and overwrite investor field for all entries.
16. Added `find_people_at_funds()` to search Attio People records for contacts at known investor funds, writing results to `potential_investor_contact`.
17. Added `--list-fields` flag to print all Attio watchlist field slugs for debugging.
18. Batch Attio updates now retry field-by-field on failure to isolate bad fields without blocking the full update.
