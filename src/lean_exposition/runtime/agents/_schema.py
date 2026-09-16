from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def parse_output(text: str, schema: Mapping[str, Any] | None) -> Any:
    if schema is None:
        return None
    value = json.loads(text)
    import jsonschema

    jsonschema.validate(value, dict(schema))
    return value
