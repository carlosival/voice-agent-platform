import json, logging, httpx, os
from typing import Optional
from workflows.utils.helpers import json_parser, merge_dict
from jsonschema import Draft202012Validator, ValidationError

logger = logging.getLogger(__name__)


STATE_EXTRACTION_PROMPT = """
You are a conversation state extraction component.

Your task is to extract information explicitly provided
or clearly corrected by the user.

Current conversation state:
{current_state}

Allowed state fields:
{state_schema}

Latest user utterance:
{user_text}

Rules:
- Extract only information supported by the utterance.
- Do not invent missing values.
- Do not modify fields not mentioned by the user.
- Preserve existing values unless explicitly corrected.
"""


async def extractor(prompt: str,
                    schema: dict,
                    *,
                    http_client: httpx.AsyncClient,
                    provider_url: str,
                    api_key: str | None,
                    model: str,
                    temperature: float = 0.0,
                    timeout: float = 5.0,
                    ) -> dict[str, any]:
    
    url = f"{provider_url.rstrip('/')}/chat/completions"
   
    headers = {
        "Content-Type": "application/json",
    }

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a conversation state extraction engine. "
                    "Extract only information explicitly supported by "
                    "the conversation. Do not invent values. "
                    "Return valid structured data."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": temperature,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "conversation_state",
                "strict": True,
                "schema": schema,
            },
        },
    }

    validator = Draft202012Validator(schema)

    try:
        response = await http_client.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )

        response.raise_for_status()

        result = response.json()

        content = result["choices"][0]["message"]["content"]

        if not content:
            raise ValueError("Empty state extraction response")

        
        data = json.loads(content)
        
        validator.validate(data)

        return data

    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON returned by model: {e}") from e

    except ValidationError as e:
        logger.exception("State extraction failed")
    

    except httpx.TimeoutException:
        logger.warning("State extraction timed out")
        raise

    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        logger.exception("State extraction failed")
        raise


async def extract_state_updates(
    user_text: str,
    current_state: dict,
    state_schema: dict,
    http_client: httpx.AsyncClient,
    provider_url: str,
    api_key: str | None,
    model: str,
    tracing_data: Optional[dict] = None, # {"tracer": tracer, "parent_span": turn_span}
    extractor: callable = extractor,
) -> dict:
    """
    extractor is a non-streaming structured-output provider adapter.
    It must return a validated JSON object.
    """

    prompt = STATE_EXTRACTION_PROMPT.format(
        current_state=json.dumps(current_state),
        state_schema=json.dumps(state_schema),
        user_text=user_text,
    )

    result = await extractor(
        prompt=prompt,
        schema=state_schema,
        http_client=http_client,
        provider_url=provider_url,
        api_key=api_key,
        model=model,
    )

    delta = result.get("updates", {})

    merge_dict(current_state, delta)

    return delta
