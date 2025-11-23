import json
import logging
from pathlib import Path
from typing import Any, Dict, Sequence

from opentelemetry.sdk.trace.export import SpanExportResult, SpanExporter


def _encode_attribute_value(val: Any) -> Dict[str, Any]:
    if isinstance(val, bool):
        return {"boolValue": val}
    if isinstance(val, int) and not isinstance(val, bool):
        return {"intValue": val}
    if isinstance(val, float):
        return {"doubleValue": val}
    return {"stringValue": str(val)}


class FileSpanExporter(SpanExporter):
    """
    Minimal OTLP-style NDJSON span exporter.
    Writes one JSON line per export call with resourceSpans/scopeSpans/spans.
    """

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence["ReadableSpan"]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS

        try:
            payload = self._to_otlp(spans)
            with self.file_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload))
                f.write("\n")
            return SpanExportResult.SUCCESS
        except Exception as exc:
            logging.error("FileSpanExporter export error: %s", exc)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return

    def _to_otlp(self, spans: Sequence["ReadableSpan"]) -> Dict[str, Any]:
        first = spans[0]
        resource_attrs = [
            {"key": k, "value": _encode_attribute_value(v)}
            for k, v in getattr(first.resource, "attributes", {}).items()
        ]
        scope_name = ""
        try:
            scope_name = first.instrumentation_scope.name
        except Exception:
            scope_name = ""

        otlp_spans = []
        for s in spans:
            attrs = [
                {"key": k, "value": _encode_attribute_value(v)}
                for k, v in getattr(s, "attributes", {}).items()
            ]
            span_dict: Dict[str, Any] = {
                "traceId": f"{s.context.trace_id:032x}",
                "spanId": f"{s.context.span_id:016x}",
                "name": s.name,
                "kind": getattr(s, "kind", ""),
                "startTimeUnixNano": getattr(s, "start_time", 0),
                "endTimeUnixNano": getattr(s, "end_time", 0),
                "attributes": attrs,
                "status": {
                    "code": getattr(getattr(s, "status", None), "status_code", ""),
                    "message": getattr(getattr(s, "status", None), "description", ""),
                },
            }
            if getattr(s, "parent", None) and getattr(s.parent, "span_id", None):
                span_dict["parentSpanId"] = f"{s.parent.span_id:016x}"
            otlp_spans.append(span_dict)

        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [
                        {
                            "scope": {"name": scope_name},
                            "spans": otlp_spans,
                        }
                    ],
                }
            ]
        }
