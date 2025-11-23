# Work Plan — 2025-11-22-a

## A) Context and Goals
- Project: Agento multi-agent, multi-module pipeline (plan generation, plan drafting + revision requests, iterative revisions).
- Target: Lake Merritt evaluation platform. Need artifacts Lake Merritt can ingest:
  - CSV files capturing initial goal (`input`) and resulting plan (`output`) with placeholder `expected_output`.
  - OpenTelemetry traces in JSON/NDJSON format that Lake Merritt’s Agento ingesters can parse (`agento_analytical_ingester` plan_delta mode and friends).
  - Keep spans rich with attributes for LLM-as-a-judge scoring and eval packs.
- Modules in scope: 1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py (Module 1), 1_06-B_Ingest-PlanStructure-to-Plan-OTEL-Semantic-OI-withComments.py (Module 2), 2_Revise-Plan-Stable-OTEL.py (Module 3).
- Outputs go to `data/` with timestamps; no reliance on external collectors—emit local OTLP-style NDJSON traces for upload.
- Additional requirement: emit a “holistic_review” style span for the pre-revision plan (end of Module 2) so later evals can compare pre-revision vs. final revision.

## B) Coding Plan (assumptions noted)
- Add a shared lightweight OTLP/NDJSON file exporter (e.g., FileSpanExporter) that serializes ReadableSpan batches to OTLP-like `resourceSpans/scopeSpans/spans` lines in `data/<module>_otel_<ts>.ndjson`. Assumption: Lake Merritt ingesters accept this simplified OTLP shape; attributes list with `{key, value:{stringValue|intValue|boolValue|doubleValue}}` is sufficient.
- Wire SimpleSpanProcessor(FileSpanExporter) alongside existing BatchSpanProcessor(OTLPSpanExporter) in all three modules. Assumption: current OTLP exporter is aimed at localhost:4317; file exporter provides the uploadable trace.
- CSV emission per module:
  - Columns: `input`, `output`, `expected_output` (placeholder “expected output goes here”).
  - Module 1: input = user goal; output = plan structure JSON string.
  - Module 2: input = original goal; output = compiled project_plan JSON string (pre-revision “final” from this module).
  - Module 3: input = original goal; output = final revised plan JSON string.
- Trace continuity: continue using `trace.context` propagation so `plan_delta` can pair the first plan (Module 1, step_type=plan) with the final plan (Module 3, step_type=holistic_review). Add a pre-revision holistic span in Module 2 with step_type `holistic_review_pre` to keep it distinct from the final.
- Holistic spans:
  - Module 2: add span `agento.event.holistic_review_pre` with `agento.step_type=holistic_review_pre`, attributes for user_goal and pre-revision plan JSON.
  - Module 3: ensure final holistic span carries `agento.step_type=holistic_review` and `gen_ai.response.content`/`agento.final_plan_content` for plan_delta ingestion.
- File names: timestamped in `data/` (e.g., `module1_plan_<ts>.csv`, `module1_otel_<ts>.ndjson`, etc.).

## C) Test Plan
- Static checks: run each module briefly with provided `.env` but mock/skip heavy LLM calls if possible (may need to set a dry-run flag if available; otherwise, light manual smoke run with the existing goal).
- Artifact checks:
  - Verify CSV files created with correct columns and non-empty rows.
  - Verify NDJSON traces exist and include `resourceSpans[0].scopeSpans[0].spans` with expected attributes (`agento.step_type`, `agento.user_goal`, `gen_ai.response.content`).
  - Confirm `traceId` continuity across modules by reading `trace.context` and ensuring exported spans share the same trace id (manual inspection on sample run).
  - Confirm pre-revision holistic span present after Module 2 and final holistic span after Module 3.
- If LLM calls are unavoidable, keep to a single short run; otherwise, verify exporter logic by unit-ish inspection of generated NDJSON structure.

## D) Observations / Notes
- Lake Merritt eval pack “Plan Quality 5‑Point Judge” uses `agento_analytical_ingester.py` mode `plan_delta`, requiring a first plan (step_type=plan) and final plan (step_type=holistic_review). We must not overwrite final plan span with the pre-revision span, hence the distinct `holistic_review_pre` step_type.
- The ingester can also consume draft/critique/revision spans if attributes are present; our file exporter should faithfully serialize span attributes to keep flexibility for other eval packs.
- No external collector found locally, so a local NDJSON exporter is mandatory.
