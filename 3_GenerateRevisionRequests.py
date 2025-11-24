#!/usr/bin/env python3
"""
Module 3 — Drafts → Revision Requests
Reads drafts (project_plan_drafts.json) and generates revision_requests only.
Outputs:
- data/project_plan_with_revisions.json (and timestamped copy)
- data/<module>_plan_<ts>.csv
- data/logs/<module>_<ts>.log
- data/<module>_otel_<ts>.ndjson
- data/for-lake-merritt/<module>_<date>-RevisionRequests.md (final markdown)
"""
import csv
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
import openai
from tqdm import tqdm
from google.api_core import retry
from google.api_core import exceptions as google_exceptions

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from openinference.semconv.trace import OpenInferenceSpanKindValues as OIKind
from pydantic import BaseModel, Field, ValidationError

from otel_file_exporter import FileSpanExporter

MODULE_NAME = Path(__file__).stem
TS_STAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
DATA_DIR = Path("data")
LM_DIR = DATA_DIR / "for-lake-merritt"
LOG_DIR = Path("logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LM_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
FILE_EXPORT_PATH = DATA_DIR / f"{MODULE_NAME}_otel_{TS_STAMP}.ndjson"

load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = openai.OpenAI(api_key=openai_api_key)

resource = Resource.create(
    {
        "service.name": "agento",
        "service.version": "1.0.0",
        "agento.module": MODULE_NAME,
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    }
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            insecure=True,
        ),
        max_export_batch_size=512,
    )
)
provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(str(FILE_EXPORT_PATH))))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filename=LOG_DIR / f"{MODULE_NAME}_{TS_STAMP}.log",
    filemode="w",
)

class ProjectPlan(BaseModel):
    Original_Goal: str
    Title: str
    Overall_Summary: str
    Detailed_Outline: list[Dict[str, str]] = Field(..., description="List of steps with content")
    Evaluation_Criteria: Dict[str, str]
    revision_requests: Optional[Dict[str, str]] = Field(None, description="Revision suggestions for each step")
    Success_Measures: list[str]

def flush():
    sys.stdout.flush()

def set_ai_response_attribute(span, txt: str):
    if len(txt) > 8192:
        span.set_attribute("gen_ai.response.truncated", True)
        span.set_attribute("agento.response.truncated_reason", "size_limit")
        span.set_attribute("agento.response.length", len(txt))
        txt = txt[:8000] + "...[truncated]"
    span.set_attribute("gen_ai.response.content", txt)

def set_openai_tokens(span, response):
    if hasattr(response, "usage") and response.usage:
        span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)

def load_plan(filename: str = "project_plan_drafts.json") -> Optional[Dict]:
    candidate_paths = [Path(filename), DATA_DIR / filename]
    path_to_use = next((p for p in candidate_paths if p.exists()), None)
    if not path_to_use:
        logging.error(f"Could not find {filename}")
        return None
    try:
        with open(path_to_use, "r", encoding="utf-8") as f:
            raw_plan = json.load(f)
        validated = ProjectPlan(**raw_plan)
        return validated.model_dump()
    except Exception as e:
        logging.error(f"Error loading plan drafts: {e}")
        return None

def generate_revision_requests(drafts, plan, original_goal):
    revision_requests = {}
    if not drafts or not plan:
        logging.error("No drafts or plan to process.")
        return revision_requests

    requests_per_minute = 300
    delay_in_seconds = 60.0 / requests_per_minute

    for step, draft in tqdm(drafts.items(), desc="Generating revision requests..."):
        current_plan_item_content = next((item["content"] for item in plan.get("Detailed_Outline", []) if item["name"] == step), "Instructions not found")
        prompt = f"""CONTEXT: You are an experienced professional evaluator who prizes practical, actionable feedback and advice and who provides clear and high quality focused outputs that follow instructions to the letter. You will evaluate the following specific draft content for a "step" in a broader project and provide me with your recommended revisions in order for the draft to better achieve the user's goal for this part of the overall work. First I will provide you further context and then a more specific instruction:

CONTEXT: THIS IS THE SPECIFIC CONTENT YOU ARE TO EVALUATE AND RECOMMEND REVISIONS FOR: {step}:
{draft}

CONTEXT: Silently consider to yourself the following user goal for this work to ensure your work on this part is well aligned to achieve the goal and do this before you decide on and provide your recommendations for revisions to the draft for this step of the project: {original_goal}

CONTEXT: Silently consider to yourself the following broader context before you decide on and provide the deliverable for this step of the project: {json.dumps(plan)}

YOUR INSTRUCTION: Given all this information, now write specific suggestions for improvement of the draft content for this step of the project. Focus on key areas that need revision to better align with the user's original goal, adhere to the evaluation criteria for this step, and that make sense in the context of the entire project, based on the broader context you now have. Do not provide a refined draft, do not provide recommended revisions for other steps, only provide your recommended content revisions requests for the draft content in the following step: {step}:
{draft}"""

        with tracer.start_as_current_span(
            f"llm.openai.generate_revision_request.{step.replace(' ', '_')}",
            kind=SpanKind.CLIENT,
            attributes={
                "openinference.span.kind": OIKind.LLM.value,
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4",
                "gen_ai.operation.name": "chat",
                "agento.step_type": "critique",
                "agento.step_name": step,
                "agento.user_goal": original_goal,
                "agento.draft_content": draft,
                "agento.plan_item": current_plan_item_content,
            },
        ) as span:
            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": prompt}]
                )
                set_openai_tokens(span, response)
                revision_content = response.choices[0].message.content
                set_ai_response_attribute(span, revision_content)
                span.set_status(Status(StatusCode.OK))

                revision_requests[step] = revision_content
                time.sleep(delay_in_seconds)

            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logging.error(f"Error calling OpenAI API for step '{step}': {e}")
                revision_requests[step] = "[error] could not generate revision"

    return revision_requests

def save_outputs(plan: Dict, revision_requests: Dict[str, str]):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    plan_with_revs = plan.copy()
    plan_with_revs["revision_requests"] = revision_requests

    json_main = DATA_DIR / "project_plan_with_revisions.json"
    json_ts = DATA_DIR / f"project_plan_with_revisions_{ts}.json"
    for path in [json_main, json_ts]:
        path.write_text(json.dumps(plan_with_revs, indent=2), encoding="utf-8")
        logging.info("Saved %s", path)

    csv_path = DATA_DIR / f"{MODULE_NAME}_plan_{ts}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["input", "output", "expected_output"])
        writer.writerow([plan.get("Original_Goal", ""), json.dumps(plan_with_revs, ensure_ascii=False), "expected output goes here"])
    logging.info("Saved CSV %s", csv_path)

    md_path = LM_DIR / f"{MODULE_NAME}-{ts}-RevisionRequests.md"
    lines = ["# Revision Requests", ""]
    for step, req in revision_requests.items():
        lines.append(f"## {step}")
        lines.append(req)
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    logging.info("Saved markdown %s", md_path)

def read_trace_context():
    try:
        ctx_path = DATA_DIR / "trace.context"
        if ctx_path.exists():
            with ctx_path.open("r", encoding="utf-8") as f:
                carrier = json.load(f)
            return propagate.extract(carrier)
    except Exception as e:
        logging.warning(f"Could not read trace.context: {e}")
    return None

def write_trace_context():
    try:
        carrier = {}
        propagate.inject(carrier)
        (DATA_DIR / "trace.context").write_text(json.dumps(carrier))
    except Exception as e:
        logging.warning(f"Could not write trace.context: {e}")

def main():
    parent_context = read_trace_context()
    with tracer.start_as_current_span(
        "agento.pipeline.generate_revision_requests",
        context=parent_context,
        kind=SpanKind.INTERNAL,
        attributes={"openinference.span.kind": OIKind.AGENT.value},
    ) as root_span:
        print("Loading drafts plan...")
        plan = load_plan()
        if plan is None:
            print("Error: could not load drafts plan.")
            root_span.set_status(Status(StatusCode.ERROR, "load drafts failed"))
            return

        goal = plan.get("Original_Goal", "")
        root_span.set_attribute("user_goal", goal)

        drafts = {item["name"]: item.get("content", "") for item in plan.get("Detailed_Outline", [])}
        revision_requests = generate_revision_requests(drafts, plan, goal)

        save_outputs(plan, revision_requests)
        write_trace_context()

    trace.get_tracer_provider().force_flush()

if __name__ == "__main__":
    import time
    main()
