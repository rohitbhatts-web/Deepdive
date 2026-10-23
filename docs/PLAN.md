# DeepDive plan

Goal: a user asks a research question; distinct agents plan, gather real web evidence, draft atomic claims,
verify each claim against its cited passages, research specific gaps, and produce a structured report with an
inspectable decision trail.

## Architecture (agreed)

- FastAPI backend; Streamlit UI calls backend endpoints only.
- LangGraph with explicit nodes and a conditional research loop.
- SQLite with foreign keys; schema in `backend/db.py`.
- All LLM calls go through `backend/mesh_client.py` (Mesh API, `MESH_API_KEY` from env). Model IDs are
  configurable; drafter and verifier models are independent. Verify a model is available before choosing it.
- Pydantic schemas for agent inputs/outputs. Store concise decision explanations and evidence references,
  never private chain-of-thought.
- No embeddings or vector DB unless justified later.
- Retrieved content is untrusted data, never instructions.

Flow: Planner -> Researcher -> Atomic Drafter -> Integrity Checks -> Semantic Verifier -> Coverage Router ->
[targeted Researcher loop (one initial pass + at most two corrective) OR constrained Synthesizer] -> Final Report.

Synthesizer rule: selects and orders accepted claim IDs only; code renders their exact verified text and
citations. No free-form factual prose. Any later paraphrasing must be re-verified.

## Phases

| # | Scope | Status |
|---|---|---|
| 1 | SQLite evidence storage; safe public-page ingestion; run/source/passage/event API | **done** |
| 2 | Planner + Tavily search + Researcher + LangGraph flow; logs queries, selections, failures, model usage; budgets | **done** (fixture-tested; a live run needs real keys) |
| 3 | Atomic Drafter, deterministic integrity checks, semantic Verifier; save rejected claims | **implemented** (see docs/PHASE3_REVIEW.md; claim revisions are Phase 4) |
| 4 | Coverage assessment, targeted research loop, budgets (requests/tokens/time), constrained synthesis, final report validation | **implemented** (see docs/PHASE4_REVIEW.md; one bounded live run) |
| 5 | Streamlit Report / Evidence / Run history views backed by real API data | |
| 6 | 10-12 questions, ~40 human-labelled claim-evidence examples, verification on vs off, measured precision/coverage/latency/usage with sample sizes; contradiction detection and source-quality heuristics if core is reliable; docs, demo, fresh-install check, submission | |

## Phase 1 notes

- Passages are exact slices of the stored extracted snapshot (`snapshot[start:end] == text`); IDs are `S{source}-P{n}`.
- Extraction/splitting is deterministic (stdlib `html.parser`, no LLM). PDFs are out of scope.
- Known gaps: DNS rebinding between validation and connect; boilerplate not removed by tag (e.g. Wikipedia
  navigation boxes are `div`/`table` content and appear as passages); no JavaScript rendering; no robots.txt handling.

## Phase 2 notes

- **Search provider: Tavily** (`POST https://api.tavily.com/search`, Bearer key, `search_depth=basic` = 1 credit/search).
  Free tier: 1,000 credits/month, no card (from Tavily docs/pricing pages, Oct 2026). Paid about $0.008/credit.
  Brave was rejected: its free tier is unclear and its terms restrict storing results.
  Snippets only help choose pages; the fetched page text is the evidence.
- Flow (`backend/graph.py`): `planner -> researcher (loops once per subquestion) -> finish`; a conditional edge ends
  the run early on a budget stop or node failure. Researcher = 2 model calls per subquestion (queries, URL
  selection) + searches + `ingest_url`. The model may only select URLs that the search returned.
- Config: `MESH_API_KEY`, `TAVILY_API_KEY`, `PLANNER_MODEL`, `RESEARCHER_MODEL` (no defaults; models are checked
  against Mesh `/models` before a run starts). Missing -> `503 not_configured` and the run stays startable.
- Run statuses: created -> researching -> collection_finished | stopped (budget; `stop_reason`) | failed (`stop_reason`).
  Subquestion statuses: open, researching, then evidence_collected | collection_incomplete | failed (collection only; never coverage or support).
- Limits (Settings): model requests 20, search queries 8, page fetches 12, output tokens 1500/call and 15000 total,
  elapsed 300 s, 4 subquestions, 2 queries/subquestion, 5 results/query, 3 URLs/subquestion.
- Audited: see `docs/PHASE2_REVIEW.md`. Mesh client has SDK retries off and a 60 s timeout; secrets are redacted from stored errors.
- Known gaps: research runs in a FastAPI background task (in-process; a restart marks the run failed);
  `max_tokens` is sent as-is, so a Mesh model that wants `max_completion_tokens` would need a one-line change;
  no live run done yet (local `.env` still holds the placeholder Mesh key and has no Tavily key).

## Next milestone

Phase 3: Atomic Drafter (claims with passage IDs + quotes), deterministic integrity checks (passage exists, quote
occurs in it), semantic Verifier (supported / partially_supported / unsupported / contradicted), storing rejected
claims and revisions.

## Out of scope for now

Submission administration (the organizer needs the hackathon dashboard entry form submitted before CI results
count) is paused.
