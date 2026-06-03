# Graph Report - /Users/aria/Documents/FlutterApps/microcapanalyzer  (2026-05-27)

## Corpus Check
- Corpus is ~8,057 words - fits in a single context window. You may not need a graph.

## Summary
- 97 nodes · 210 edges · 8 communities detected
- Extraction: 78% EXTRACTED · 22% INFERRED · 0% AMBIGUOUS · INFERRED: 47 edges (avg confidence: 0.73)
- Token cost: 9,800 input · 3,200 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Market Data & Yahoo Finance|Market Data & Yahoo Finance]]
- [[_COMMUNITY_SEC EDGAR Pipeline|SEC EDGAR Pipeline]]
- [[_COMMUNITY_Data Models & Filing Parsing|Data Models & Filing Parsing]]
- [[_COMMUNITY_Orchestration & Display|Orchestration & Display]]
- [[_COMMUNITY_Research Brief & Verdict|Research Brief & Verdict]]
- [[_COMMUNITY_Metrics Display & Utilities|Metrics Display & Utilities]]
- [[_COMMUNITY_Changelog|Changelog]]
- [[_COMMUNITY_Project Rules|Project Rules]]

## God Nodes (most connected - your core abstractions)
1. `main()` - 21 edges
2. `5-step analysis pipeline` - 19 edges
3. `fetch_edgar_data()` - 13 edges
4. `_build_user_message()` - 12 edges
5. `VolumeMetrics` - 11 edges
6. `fetch_market_data()` - 11 edges
7. `EdgarData` - 10 edges
8. `fetch_peer_volume()` - 10 edges
9. `run_analysis()` - 10 edges
10. `FilingRecord` - 9 edges

## Surprising Connections (you probably didn't know these)
- `Volume pattern classifications (SILENT_ACCUMULATION, BREAKOUT_CONFIRMATION, DISTRIBUTION, VOLATILITY_EVENT)` --semantically_similar_to--> `README: volume pattern explanations`  [INFERRED] [semantically similar]
  market_data.py → README.md
- `8-section brief structure (STATISTICAL CONTEXT through FINAL VERDICT)` --semantically_similar_to--> `README: verdict type explanations`  [INFERRED] [semantically similar]
  nlp_analysis.py → README.md
- `_border_for_section()` --conceptually_related_to--> `Rationale: qualitative 3-factor dilution assessment replaced numeric score (Factor A/B/C)`  [INFERRED]
  /Users/aria/Documents/FlutterApps/microcapanalyzer/display.py → README.md
- `Rationale: mandatory 0.15s SEC sleep (10 req/s limit; silent 403 IP block risk)` --rationale_for--> `sec_sleep()`  [EXTRACTED]
  CLAUDE.md → /Users/aria/Documents/FlutterApps/microcapanalyzer/utils.py
- `Rationale: Yahoo Finance direct chart API over yfinance download (avoids 429 on crumb endpoint)` --rationale_for--> `_fetch_chart()`  [EXTRACTED]
  CLAUDE.md → /Users/aria/Documents/FlutterApps/microcapanalyzer/market_data.py

## Hyperedges (group relationships)
- **Core analysis pipeline: VolumeMetrics + EdgarData + PeerVolumeData → single Gemini call → 8-section brief** — market_data_volumemetrics, edgar_edgardata, market_data_peervolumerdata, nlp_analysis_run_analysis, nlp_analysis_8section_brief [EXTRACTED 1.00]
- **SEC rate limiting pattern: sec_sleep() called between every EDGAR request in edgar.py and check_sec_enforcement()** — utils_sec_sleep, edgar_fetch_edgar_data, edgar_check_sec_enforcement, rationale_sec_rate_limiting [EXTRACTED 1.00]
- **Form 4 dual-parsing: edgar._parse_insider_summary() feeds EdgarData.insider_summary; nlp_analysis.parse_form4_transactions() feeds prompt separately for high-conviction filter** — edgar_parse_insider_summary, nlp_analysis_parse_form4_transactions, edgar_insidersummary, edgar_filingrecord, nlp_analysis_build_user_message [INFERRED 0.88]

## Communities

### Community 0 - "Market Data & Yahoo Finance"
Cohesion: 0.15
Nodes (21): Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart), yfinance (fast_info enrichment only, best-effort), _chart_to_ohlcv(), _classify_volume_pattern(), _enrich_fundamentals(), _fetch_chart(), fetch_market_data(), fetch_peer_volume() (+13 more)

### Community 1 - "SEC EDGAR Pipeline"
Cohesion: 0.17
Nodes (19): _build_doc_url(), check_sec_enforcement(), _compute_velocity(), _extract_catalyst_dates(), fetch_edgar_data(), fetch_filing_text(), fetch_submissions(), _multiplier() (+11 more)

### Community 2 - "Data Models & Filing Parsing"
Cohesion: 0.17
Nodes (18): Entry point: orchestrates the full SEC Volume Spike Analyzer pipeline., All rich terminal formatting: panels, tables, progress, final brief., EdgarData, FilingRecord, InsiderSummary, _parse_insider_summary(), Google Gemini generativeai API (gemini-2.0-flash-lite), PeerVolumeData (+10 more)

### Community 3 - "Orchestration & Display"
Cohesion: 0.23
Nodes (16): main(), 5-step analysis pipeline, CLAUDE.md: architecture and critical code rules, console singleton (Rich Console), make_filing_progress(), print_enforcement_flags(), print_error(), print_filing_summary() (+8 more)

### Community 4 - "Research Brief & Verdict"
Cohesion: 0.29
Nodes (8): _border_for_section(), print_research_brief(), _split_sections(), 8-section brief structure (STATISTICAL CONTEXT through FINAL VERDICT), extract_verdict(), Verdict types (STRONG BUY SIGNAL, MODERATE BUY SIGNAL, HOLD FOR MORE DATA, AVOID, INSUFFICIENT DATA), Rationale: qualitative 3-factor dilution assessment replaced numeric score (Factor A/B/C), README: verdict type explanations

### Community 5 - "Metrics Display & Utilities"
Cohesion: 0.32
Nodes (7): _pct_style(), print_volume_metrics(), fmt_millions(), fmt_pct(), fmt_shares(), Shared helpers: rate limiting, text truncation, retry logic., truncate()

### Community 6 - "Changelog"
Cohesion: 1.0
Nodes (1): README: 7 recent feature updates documentation

### Community 7 - "Project Rules"
Cohesion: 1.0
Nodes (1): CLAUDE.md: 13 rules for Claude Code behavior

## Knowledge Gaps
- **24 isolated node(s):** `All SEC EDGAR API logic: CIK resolution, filing fetch, supplementary data.`, `Search EDGAR full-text for enforcement actions involving this company.`, `Shared helpers: rate limiting, text truncation, retry logic.`, `Exponential backoff retry. Raises on final failure.`, `Market data: direct Yahoo Finance chart API + position size + regime warning.` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Changelog`** (1 nodes): `README: 7 recent feature updates documentation`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Project Rules`** (1 nodes): `CLAUDE.md: 13 rules for Claude Code behavior`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `main()` connect `Orchestration & Display` to `Market Data & Yahoo Finance`, `SEC EDGAR Pipeline`, `Data Models & Filing Parsing`, `Research Brief & Verdict`, `Metrics Display & Utilities`?**
  _High betweenness centrality (0.235) - this node is a cross-community bridge._
- **Why does `5-step analysis pipeline` connect `Orchestration & Display` to `Market Data & Yahoo Finance`, `SEC EDGAR Pipeline`, `Data Models & Filing Parsing`, `Research Brief & Verdict`, `Metrics Display & Utilities`?**
  _High betweenness centrality (0.199) - this node is a cross-community bridge._
- **Why does `fetch_edgar_data()` connect `SEC EDGAR Pipeline` to `Data Models & Filing Parsing`, `Orchestration & Display`?**
  _High betweenness centrality (0.171) - this node is a cross-community bridge._
- **Are the 19 inferred relationships involving `main()` (e.g. with `get_regime_warning()` and `print_error()`) actually correct?**
  _`main()` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `_build_user_message()` (e.g. with `fmt_pct()` and `fmt_millions()`) actually correct?**
  _`_build_user_message()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `VolumeMetrics` (e.g. with `All rich terminal formatting: panels, tables, progress, final brief.` and `Single consolidated Gemini Flash API call: prompt construction and response pars`) actually correct?**
  _`VolumeMetrics` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `All SEC EDGAR API logic: CIK resolution, filing fetch, supplementary data.`, `Search EDGAR full-text for enforcement actions involving this company.`, `Shared helpers: rate limiting, text truncation, retry logic.` to the rest of the system?**
  _24 weakly-connected nodes found - possible documentation gaps or missing edges._