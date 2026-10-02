# Roadmap

Ordered by expected value. Done items are in the git history.

## Done

- Deterministic risk policy, structured rollbacks, apply-time re-check
- Run health tracking, failure alerts, nonzero exit on failed runs
- Schema-validated LLM output with validity-driven escalation
- Replay-based A/B evaluation for scan-prompt changes
- Fixed: Judge never returned `tonight_score` or `summary` (UCB1 reward was always 0)

## Next

1. **Dry-run burn-in.** Run `--dry-run` and then a few live nights; confirm health output, replay input saving, and Ollama schema-format support on the installed Ollama version.
2. **Split `dream_cycle.py` (~2,800 lines).** Target layout: `phases/`, `llm/` (ollama, claude, `llm_json`, router), `store/` (ChromaDB, lessons, UCB1), `vault/` (Obsidian mirror), `notify/`. Do after burn-in so behavior changes and refactors stay separate.
3. **Fix the broken tests.** `test_dream_cycle.py` and `test_experimentation.py` fail at import; manifest tests fail on the agent `type` enum. Remove duplicated `*_simple.py` tests.
4. **Scheduling.** Replace cron with a systemd timer (`Persistent=true` so missed nights catch up) and add a lock file to prevent overlapping runs.

## Later

- **Replay coverage beyond scan.** Extend A/B replay to the research and judge prompts; build the eval corpus from `performance.jsonl` task outcomes.
- **Outcome tracking.** Measure whether applied changes helped (escalation rate, failure rate before vs after) and feed that into Phase 2 and UCB1, not just apply/revert events.
- **Router calibration.** Log local-vs-frontier escalation rate and validity per phase; tune thresholds from data. Retire the `<<CONFIDENCE>>` self-score for non-JSON paths.
- **Single settings object.** Collapse `config.json`, `config.yaml`, env vars and `config.example.json` into one validated settings model.
- **Cost accounting.** Per-run token and dollar totals in the health file and email.
- **CI.** ruff, pytest and bandit on push.
- **Lumen auth.** The MCP server checks that a token is set, not that the caller's token matches. Fine over stdio; fix before any HTTP transport.
- **Transport hygiene.** arXiv fetch over HTTPS; pin dependency versions.
- **Packaging as a product.** The CVE and AI-governance scan track can be offered as a validated weekly brief; depends on replay evals and run health being trusted first. The staged-change, rollback and eval-delta trail also fits an audit-trail pitch.
