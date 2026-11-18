"""Hilfen fuer Tests mit geskriptetem Modell (``FunctionModel``).

Agenten mit festem Antwortschema geben ihre Antwort je nach ``OUTPUT_MODE`` als
Werkzeugaufruf oder als JSON-Text ab. Die Helfer hier bauen die passende
Antwort, damit die Tests in beiden Ausgabearten gelten.
"""

import json

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo


def wants_structure(info: AgentInfo) -> bool:
    """Ob der anfragende Agent eine Antwort nach Schema erwartet (statt freiem Text)."""
    return bool(info.output_tools) or info.model_request_parameters.output_object is not None


def structured_reply(info: AgentInfo, payload: dict) -> ModelResponse:
    """Die Antwort ``payload`` in der Form, die der Agent erwartet."""
    if info.output_tools:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])
    return ModelResponse(parts=[TextPart(json.dumps(payload, ensure_ascii=False))])
