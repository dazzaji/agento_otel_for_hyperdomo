# Plan to Decouple Revision Requests into a Dedicated Module

Goal: Split the current draft-generation + revision-request flow (Module 2) into two clearer stages so the pipeline becomes:
1) Goal → Plan Outline (existing Module 1)
2) Plan Outline → First Drafts (new “Draft Builder” module, carved out of current Module 2 draft generation)
3) First Drafts → Revision Requests (new dedicated module)
4) Apply Revision Requests → Final Plan (current Module 3 logic)

Approach
- Extract the draft-generation logic from Module 2 into its own module (Draft Builder) that:
  - Inputs: `plan_structure.json` (from Module 1).
  - Outputs: `project_plan.json` containing drafts (no revision_requests) + CSV/OTEL/MD.
- Create a new “Revision Request Generator” module that:
  - Inputs: `project_plan.json` (with drafts).
  - Outputs: `project_plan_with_revisions.json` (adds `revision_requests`), CSV/OTEL, MD summary of requests.
- Modify Module 3 to consume `project_plan_with_revisions.json` and skip any draft-generation concerns.

Data Flow
- Module 1: writes `data/plan_structure.json` (+ timestamped).
- Draft Builder (new): reads `data/plan_structure.json`, writes `data/project_plan_drafts.json` (+ timestamped), markdown of drafts to `data/for-lake-merritt/`.
- Revision Request Generator (new): reads `data/project_plan_drafts.json`, writes `data/project_plan_with_revisions.json` (+ timestamped), markdown of revision requests to `data/for-lake-merritt/`.
- Module 3: reads `data/project_plan_with_revisions.json`, writes revised outputs/markdown to `data/` and final MD to `data/for-lake-merritt/`.

Telemetry/CSV
- Keep existing OTEL NDJSON export per module to `data/<module>_otel_<ts>.ndjson`.
- Keep Lake Merritt CSV per module in `data/` with timestamps.
- Only final markdown per module goes to `data/for-lake-merritt/`.

Changes Needed
- New module: Draft Builder (clone draft portion of current Module 2; drop revision generation).
- New module: Revision Request Generator (clone revision-request portion of current Module 2; assumes drafts already exist).
- Update Module 3: consume `project_plan_with_revisions.json`; adjust load paths and messaging.
- Update docs/run instructions to reflect 4-step pipeline.

Open Questions
- Do we want any lightweight validation between modules (e.g., ensure drafts exist before generating revisions)?
- Should we persist a “latest” symlink/marker for each artifact to simplify chaining?
