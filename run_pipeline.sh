#!/usr/bin/env bash
set -euo pipefail

# Run full Agento pipeline: goal->plan outline->drafts->revision requests->apply revisions.
# Assumes venv is activated and API keys are set.

python 1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py
python 1_06-B_Ingest-PlanStructure-to-Plan-OTEL-Semantic-OI-withComments.py
python 3_GenerateRevisionRequests.py
python 2_Revise-Plan-Stable-OTEL.py
