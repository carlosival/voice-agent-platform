from copy import deepcopy
import json
from typing import Optional


def json_parser(json_str: str) -> Optional[dict[str, any]]:
            """
            Try to parse a JSON string to Dict.
            If it fails, attempt to extract the first {...} block.
            If that also fails, try to parse it as a Python literal.
            """
            try:
                # First try: normal JSON parsing
                json_dict = json.loads(json_str)
                if isinstance(json_dict, dict):
                    return json_dict
                else:
                    raise ValueError("Top-level JSON is not an object")
            
            except json.JSONDecodeError:
                # Second try: extract first {...} using regex
                match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if match:
                    try:
                        json_dict = json.loads(match.group(0))
                        if isinstance(json_dict, dict):
                            return json_dict
                    except json.JSONDecodeError:
                        pass  # Continue to literal_eval fallback

                # Third try: attempt to parse as Python literal
                try:
                    python_dict = ast.literal_eval(json_str)
                    if isinstance(python_dict, dict):
                        # Convert to JSON-compatible: ensure keys are strings etc.
                        json_string = json.dumps(python_dict)
                        json_dict = json.loads(json_string)
                        return json_dict
                except (ValueError, SyntaxError):
                    pass

            # If everything fails, return a fallback
            logger.warning(f"JSON parsing failed for: {json_str[:100]}...")
            
            raise ValueError("Error parsing str as json")


def schema_to_dict(schema: dict):
    """Build an empty state skeleton from a JSON schema."""
    if "default" in schema:
        return deepcopy(schema["default"])

    type_ = schema.get("type", None)
    if type_ == "object":
        return {k: schema_to_dict(v) for k, v in schema.get("properties", {}).items()}
    if type_ == "array":
        return []
    return None  # string, number, integer, boolean, or unknown


def merge_dict(state: dict, update: dict) -> dict:
    """Merge an LLM-proposed update into the state, ignoring unknown keys
    and never overwriting a known value with null."""
    for key, value in update.items():
        if key not in state:
            continue  # unknown field: ignore
        if isinstance(state[key], dict) and isinstance(value, dict):
            merge_dict(state[key], value)
        elif value is not None:
            state[key] = value
    return state

def deep_merge(base: dict, patch: dict) -> dict:
    result = base.copy()

    for key, value in patch.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result