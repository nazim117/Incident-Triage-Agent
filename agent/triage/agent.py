"""
The investigation loop: the model calls tools until it has enough evidence,
then returns a JSON diagnosis.

This is the standard "tool calling" agent pattern, written out by hand
instead of hidden inside a framework so each step is visible:

  1. Send the system prompt + the firing alerts, along with the tool schemas.
  2. If the reply contains tool_calls, run each one and append its result as
     a `role: tool` message, then call the model again with the longer
     conversation.
  3. If the reply has no tool_calls, it's the final answer: parse the JSON.

The model is stateless between calls - the growing `messages` list IS the
agent's memory of the investigation, which is why tools.py caps result size.
"""

import json
import re
import time
from dataclasses import asdict, dataclass, field

from .prompts import FORCE_FINAL_MESSAGE, JSON_RETRY_MESSAGE, SYSTEM_PROMPT, incident_message
from .tools import TOOL_SCHEMAS, Toolbox

# How much of each tool result to keep in the report's transcript. The model
# saw the full (already capped) result; the report only needs a preview.
TRANSCRIPT_PREVIEW_CHARS = 800


@dataclass
class ToolCallRecord:
    name: str
    arguments: str
    result_preview: str


@dataclass
class Diagnosis:
    alerts: list[dict]
    summary: str = ""
    root_cause: str = ""
    affected_services: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    confidence: str = "low"
    suggested_remediation: list[dict] = field(default_factory=list)
    notes: str = ""
    model: str = ""
    steps: int = 0
    duration_seconds: float = 0.0
    # Summed over every model call in the investigation. Each call re-sends
    # the whole conversation, so prompt tokens grow with every step - the
    # main reason the eval tracks steps and tokens side by side.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_json_object(text: str | None) -> dict | None:
    """Extracts the JSON object from a model reply. Models sometimes wrap it
    in ```json fences or add a sentence before it despite instructions, so
    fall back to the outermost {...} span."""
    if not text:
        return None
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


class TriageAgent:
    def __init__(self, client, model: str, toolbox: Toolbox, max_steps: int = 12, log=print):
        # `client` is an openai.OpenAI instance pointed at DeepSeek - or, in
        # tests, any object with the same .chat.completions.create shape.
        self.client = client
        self.model = model
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.log = log

    def _complete(self, diagnosis: Diagnosis, messages: list[dict], **kwargs):
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            # Low temperature: this is diagnosis, not creative writing. It
            # also makes repeated runs on the same incident more comparable.
            temperature=0.1,
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            diagnosis.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            diagnosis.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        return resp.choices[0].message

    def triage(self, alerts: list[dict]) -> Diagnosis:
        started = time.monotonic()
        diagnosis = Diagnosis(alerts=alerts, model=self.model)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": incident_message(alerts)},
        ]

        reply = None
        for step in range(1, self.max_steps + 1):
            diagnosis.steps = step
            reply = self._complete(diagnosis, messages)
            if not reply.tool_calls:
                break
            # The assistant turn that requested the tools must be in the
            # history before the tool results, or the API rejects the
            # request: every tool message answers a specific tool_call_id.
            messages.append(_assistant_message(reply))
            for call in reply.tool_calls:
                name, args = call.function.name, call.function.arguments
                self.log(f"  -> {name}({args})")
                result = self.toolbox.dispatch(name, args)
                diagnosis.tool_calls.append(
                    ToolCallRecord(name, args, result[:TRANSCRIPT_PREVIEW_CHARS])
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        else:
            # Step budget exhausted while the model still wanted tools. One
            # last call with tool_choice="none" forces a text answer from
            # whatever evidence it has, instead of failing with nothing.
            self.log(f"  step limit ({self.max_steps}) reached, asking for a final answer")
            messages.append({"role": "user", "content": FORCE_FINAL_MESSAGE})
            reply = self._complete(diagnosis, messages, tool_choice="none")

        parsed = parse_json_object(reply.content)
        if parsed is None:
            messages.append({"role": "assistant", "content": reply.content or ""})
            messages.append({"role": "user", "content": JSON_RETRY_MESSAGE})
            retry = self._complete(diagnosis, messages, tool_choice="none")
            parsed = parse_json_object(retry.content)
            if parsed is None:
                diagnosis.notes = "Model did not return valid JSON. Raw reply:\n" + (retry.content or "")
                parsed = {}

        _apply(diagnosis, parsed)
        diagnosis.duration_seconds = round(time.monotonic() - started, 1)
        return diagnosis


def _assistant_message(reply) -> dict:
    return {
        "role": "assistant",
        "content": reply.content or "",
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in reply.tool_calls
        ],
    }


def _apply(diagnosis: Diagnosis, parsed: dict) -> None:
    """Copies model output onto the Diagnosis, coercing types so a slightly
    off-schema reply (a string where a list was asked for) still renders."""
    for key in ("summary", "root_cause", "notes"):
        if parsed.get(key):
            diagnosis.__dict__[key] = str(parsed[key])
    if str(parsed.get("confidence", "")).lower() in ("low", "medium", "high"):
        diagnosis.confidence = parsed["confidence"].lower()
    diagnosis.affected_services = [str(s) for s in _as_list(parsed.get("affected_services"))]
    diagnosis.evidence = [
        e if isinstance(e, dict) else {"source": "", "detail": str(e)}
        for e in _as_list(parsed.get("evidence"))
    ]
    diagnosis.suggested_remediation = [
        r if isinstance(r, dict) else {"command": str(r), "why": ""}
        for r in _as_list(parsed.get("suggested_remediation"))
    ]


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
