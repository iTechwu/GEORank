---
name: georank-mcp
description: Operate GEOrank through its MCP tools (mcp__georank__*), which run in-process with system-level AI access and need no HTTP login. Use when the GEORank MCP server is mounted (dsh-georank-mcp bundle) and the task is company lookup, GEO diagnosis, keyword expansion, solution Q&A, expert/content reading, or the structured generators (JSON-LD, llms.txt, title, knowledge base, AI-friendliness score). Distinct from the HTTP-API "georank" skill, which requires login and uses scripts/georank_client.py.
---

# GEORank MCP

GEORank MCP exposes the GEO workbench (companies, diagnosis, keyword expansion,
solution Q&A, experts, content, settings, usage, and structured-content tools) as
MCP tools available to the agent. The MCP server runs inside GEORank's backend
and uses **system-level AI access**, so calls need no user session or HTTP login.

Bind to the GEORank MCP server via the `dsh-georank-mcp` DSH bundle; tool calls
are prefixed `mcp__georank__`.

## Tool surface

Reads (no AI, no write):
- `georank_list_companies(query, category, page, size, sort)` — list published GEO companies/brands.
- `georank_get_company(identifier)` — company detail by path_key / slug / url.
- `georank_company_similar(identifier, limit)` — same-domain companies.
- `georank_company_pipeline_status(company_id)` — ingestion pipeline state.
- `georank_get_diagnostic_report(report_id)` — full report (schema/content/meta/citation + recommendations).
- `georank_diagnostic_history(limit)` — recent reports.
- `georank_solution_channels()` — available GEO solution channels + sample questions.
- `georank_list_experts(category)` / `georank_get_expert(slug)` — published expert profiles.
- `georank_list_content(content_type, limit)` / `georank_get_content(slug)` — published tutorials/content.
- `georank_get_public_settings()` — public site config.

AI-backed (needs a configured model provider):
- `georank_diagnose_url(url, company_id)` — submit a site for GEO diagnosis.
- `georank_expand_keywords(seeds)` — expand seed terms into question/scenario/commercial/recommendation assets.
- `georank_solution_chat(question, channel)` — channel-context GEO Q&A.

Structured generators (deterministic v1):
- `georank_generate_jsonld(brief)`, `georank_generate_llms_txt(brief)`,
  `georank_generate_title(brief)`, `georank_generate_knowledge_base(brief)`,
  `georank_score_ai_friendliness(brief)`.

## Guidance

1. Confirm the target/existing company with `georank_get_company` or
   `georank_list_companies` before generating content about it.
2. **Diagnosis is asynchronous.** `georank_diagnose_url` returns a `report_id`
   immediately; poll `georank_get_diagnostic_report` until `status` is
   `completed` (or `failed`). Never present a diagnostic result as final without
   polling to completion.
3. Generative tools require a configured model provider (LLM_API_KEY / API pool).
   If a call errors, report the reason rather than fabricating an answer.
4. Use generation tools as drafting aids; treat their output as a starting point,
   not a claim about any AI platform's behavior.
5. Prefer reads for evidence; only write (`georank_diagnose_url`,
   `georank_expand_keywords`) when the task genuinely needs to create GEO assets.

## Notes

- MCP tools run with system-level access and no per-user quota/auth; this differs
  from the HTTP-API skill, which logs in as a user and enforces admin roles.
- The standalone MCP process (georank-mcp) exposes only `/mcp`; the in-app
  endpoint is `/mcp` on the GEORank API.
