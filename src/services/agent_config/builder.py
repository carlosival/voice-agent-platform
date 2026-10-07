import copy, re, hashlib, json
from datetime import datetime, timezone
from typing import Any, Optional

'''
    Builder is responsable to serialize AGENT ORM object to JSON Schema Version.X
    JSON Schema will be uses for all Workers through deps providers to Provide the ExecContext and run the workflows.
'''



SCHEMA_VERSION = 1

# The ONLY things a user override may touch. Everything else
# (meta, access, prompt, ids, schemas) is unreachable by construction.
OVERRIDABLE_MODEL_KEYS = {
    "llm": {"temperature", "max_tokens", "top_p"},
    "tts": {"speed", "voice_id"},
    "stt": {"language"},
}


# key -> expected type. Anything not listed is dropped.
OVERRIDABLE_CUSTOM_KEYS: dict[str, type] = {
    "greeting": str,
    "max_call_seconds": int,
    "transfer_number": str,
    "business_hours": dict,
}

OVERRIDABLE_TOOL_ATTRS = {"enabled", "config"}

EXPECTED_ROOT_KEYS = {"schema_version", "meta", "access", "models", "prompt", "input","state", "tools","custom"}
SECRET_HINTS = ("api_key", "apikey", "secret", "password", "token")


class AgentConfigError(Exception):
    """The agent can't be run as configured (missing required value, duplicate tool...)."""


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _json_type_ok(value: Any, t: Any) -> bool:
    if t is None:
        return True
    if isinstance(t, list):
        return any(_json_type_ok(value, x) for x in t)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = {"string": str, "boolean": bool, "object": dict, "array": list}.get(t)
    return py is None or isinstance(value, py)


def _resolve_input(schema: Any, patch: dict) -> dict[str, Any]:
    schema = _as_dict(schema)
    props = _as_dict(schema.get("properties"))
    required = set(schema.get("required") or [])

    unknown = set(patch) - set(props)
    if unknown:
        raise AgentConfigError(f"input has keys not in input_schema: {sorted(unknown)}")

    values: dict[str, Any] = {}
    for name, spec in props.items():
        spec = _as_dict(spec)
        if name in patch:
            v = patch[name]
        elif "default" in spec:
            v = spec["default"]
        elif name in required:
            raise AgentConfigError(f"input.{name} is required by input_schema")
        else:
            continue

        if not _json_type_ok(v, spec.get("type")):
            raise AgentConfigError(f"input.{name} must be of type {spec.get('type')}")
        if "enum" in spec and v not in spec["enum"]:
            raise AgentConfigError(f"input.{name} must be one of {spec['enum']}")
        values[name] = copy.deepcopy(v)
    return values

PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
IDENT_RE = re.compile(r"^\w+$")


def _template_placeholders(template: str) -> tuple[set[str], list[str]]:
    """Returns (valid names, malformed raw placeholders)."""
    names, bad = set(), []
    for raw in PLACEHOLDER_RE.findall(template or ""):
        name = raw.strip()
        if IDENT_RE.match(name):
            names.add(name)
        else:
            bad.append(raw)          # e.g. {{ user.name }}, {{ }}, {{a|upper}}
    return names, bad


def build_agent_config(assoc) -> dict[str, Any]:
    agent = assoc.agent
    pk_id = assoc.public_key_id
    pv = agent.prompt_version
    state = pv.state
    override = _as_dict(assoc.custom_config_override)

    
    # ---- state: schema of the values the LLM tracks at runtime ---------
    fields: dict[str, dict] = {}
    for a in (state.associated_fields if state else []):
        fields[a.field.name] = {
            "type": a.field.type,
            "description": a.field.description,
            "required": a.required,  # runtime meaning: must be filled before call ends / tool fires
        }

    # ---- models --------------------------------------------------------
    models = {
        "llm": copy.deepcopy(agent.llm_config),
        "tts": copy.deepcopy(agent.tts_config),
        "stt": copy.deepcopy(agent.stt_config),
    }
    models_patch = _as_dict(override.get("models"))
    for kind, allowed in OVERRIDABLE_MODEL_KEYS.items():
        patch = _as_dict(models_patch.get(kind))
        models[kind].update({k: v for k, v in patch.items() if k in allowed})

    # ---- tools ---------------------------------------------------------
    tools_patch = _as_dict(override.get("tools"))
    tools: dict[str, dict] = {}
    for assoc_tool in agent.agent_tools:
        if not assoc_tool.enabled:
            continue
        tool = assoc_tool.tool
        if tool.name in tools:
            raise AgentConfigError(f"Duplicate tool name on agent: {tool.name}")

        patch = {
            k: v
            for k, v in _as_dict(tools_patch.get(tool.name)).items()
            if k in OVERRIDABLE_TOOL_ATTRS
        }
        if patch.get("enabled") is False:
            continue  # user turned it off

        config = copy.deepcopy(assoc_tool.config)
        if isinstance(patch.get("config"), dict):
            config = _deep_merge(config, patch["config"])

        tools[tool.name] = {
            "id": str(tool.id),
            "description": tool.description,
            "parameters": copy.deepcopy(tool.input_schema),
            "output_schema": copy.deepcopy(tool.output_schema) or {},
            "config": config,  # runtime settings, never sent to the LLM
        }


    # ---- custom --------------------------------------------------------
    custom: dict[str, Any] = {}
    for k, v in _as_dict(override.get("custom")).items():
        expected = OVERRIDABLE_CUSTOM_KEYS.get(k)
        if expected is None:
            continue  # unknown key: ignored (or raise AgentConfigError if you prefer strict)
        if not isinstance(v, expected) or isinstance(v, bool) and expected is int:
            raise AgentConfigError(f"custom.{k} must be {expected.__name__}")
        custom[k] = copy.deepcopy(v)


    # ---- input: values for prompt.input_schema -------------------------
    input_values = _resolve_input(pv.input_schema, _as_dict(override.get("input")))


    # ---- assemble ------------------------------------------------------
    body = {
        "schema_version": SCHEMA_VERSION,
        "access": {
            "tier": assoc.tier,
            "regions": list(assoc.region),
            "allowed_domains": list(assoc.allowed_domains),
        },
        "models": models,
        "prompt": {
            "id": str(pv.id),
            "version": pv.version,
            "template": pv.content,
            "input_schema": copy.deepcopy(pv.input_schema),
            "output_schema": copy.deepcopy(pv.output_schema),
        },
        "input": input_values,
        "state": {"fields": fields},
        "tools": tools,
        "custom": custom,
    }
    config_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    return {
        "meta": {
            "agent_id": str(agent.id),
            "pk_id": str(pk_id),
            "name": agent.name,
            "strategy": agent.strategy,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config_hash": config_hash,
        },
        **body,
    }

def validate_agent_config(cfg: dict) -> list[str]:
    """Returns a list of problems. Empty list means the config is valid."""
    errs: list[str] = []

    def need(cond: bool, msg: str):
        if not cond:
            errs.append(msg)

    # root
    need(set(cfg) == EXPECTED_ROOT_KEYS,
         f"root keys mismatch: extra={set(cfg) - EXPECTED_ROOT_KEYS}, missing={EXPECTED_ROOT_KEYS - set(cfg)}")
    need(cfg.get("schema_version") == SCHEMA_VERSION, "schema_version mismatch")

    # meta
    meta = cfg.get("meta", {})
    for k in ("agent_id", "name", "strategy", "generated_at", "config_hash"):
        need(isinstance(meta.get(k), str) and meta[k], f"meta.{k} must be a non-empty string")

    # access
    access = cfg.get("access", {})
    need(isinstance(access.get("tier"), str), "access.tier must be str")
    need(isinstance(access.get("regions"), list), "access.regions must be list")
    need(isinstance(access.get("allowed_domains"), list), "access.allowed_domains must be list")

    # models
    models = cfg.get("models", {})
    need(set(models) == {"llm", "tts", "stt"}, "models must have exactly llm, tts, stt")
    for kind, m in models.items():
        need(isinstance(m, dict), f"models.{kind} must be dict")

    # prompt
    prompt = cfg.get("prompt", {})
    need(set(prompt) == {"id", "version", "template", "input_schema", "output_schema"},
         f"prompt keys unexpected: {set(prompt)}")
    need(isinstance(prompt.get("version"), int), "prompt.version must be int")
    need(isinstance(prompt.get("template"), str), "prompt.template must be str")

    # state: schema of the values the LLM tracks at runtime (no values at config time)
    state = cfg.get("state", {})
    need(set(state) == {"fields"}, "state must have exactly: fields")
    fields = _as_dict(state.get("fields"))
    for name, f in fields.items():
        need(set(f) == {"type", "description", "required"},
             f"state.fields.{name} keys unexpected: {set(f)}")
        need(isinstance(f.get("required"), bool), f"state.fields.{name}.required must be bool")

   
    # tools
    for name, t in cfg.get("tools", {}).items():
        need(set(t) == {"id", "description", "parameters", "output_schema", "config"},
             f"tools.{name} keys unexpected: {set(t)}")
        need(isinstance(t.get("parameters"), dict), f"tools.{name}.parameters must be dict")
        need(isinstance(t.get("config"), dict), f"tools.{name}.config must be dict")

    # custom
    custom = cfg.get("custom", {})
    need(isinstance(custom, dict), "custom must be dict")
    need(set(custom) <= set(OVERRIDABLE_CUSTOM_KEYS),
         f"custom has unknown keys: {set(custom) - set(OVERRIDABLE_CUSTOM_KEYS)}")


    # input
    inp = cfg.get("input", {})
    need(isinstance(inp, dict), "input must be dict")
    schema_props = _as_dict(_as_dict(prompt.get("input_schema")).get("properties"))
    need(set(inp) <= set(schema_props),
         f"input has keys not in prompt.input_schema: {set(inp) - set(schema_props)}")
    for name in _as_dict(prompt.get("input_schema")).get("required") or []:
        need(name in inp, f"input.{name} is required by prompt.input_schema")
    for name, v in inp.items():
        need(_json_type_ok(v, _as_dict(schema_props.get(name)).get("type")),
             f"input.{name} has wrong type")

    # --- every template placeholder must have an input ---
    input_schema = _as_dict(prompt.get("input_schema"))
    schema_props = _as_dict(input_schema.get("properties"))
    schema_required = set(input_schema.get("required") or [])

    placeholders, malformed = _template_placeholders(prompt.get("template", ""))

    need(not malformed, f"template has malformed placeholders: {malformed}")

    for p in sorted(placeholders):
        need(p in schema_props,
             f"template uses {{{{{p}}}}} but input_schema.properties has no '{p}'")
        need(p in schema_required,
             f"template uses {{{{{p}}}}} but '{p}' is not in input_schema.required")
        need(p in inp,
             f"template uses {{{{{p}}}}} but input.{p} has no value")

    both = placeholders & set(fields)
    need(not both, f"placeholders also declared as state fields: {sorted(both)}")

    # secrets must not be stored in the config
    def scan(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if any(h in str(k).lower() for h in SECRET_HINTS) and not str(k).lower().endswith("_ref"):
                    errs.append(f"possible secret at {path}.{k}")
                scan(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                scan(v, f"{path}[{i}]")

    scan(cfg.get("models", {}), "models")
    scan(cfg.get("tools", {}), "tools")

    # must be JSON-serializable (this is what goes to Redis)
    try:
        json.dumps(cfg)
    except TypeError as e:
        errs.append(f"not JSON-serializable: {e}")

    return errs