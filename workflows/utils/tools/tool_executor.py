import json
import asyncio
from typing import TypedDict, Dict
from smolagents import Tool, PythonInterpreterTool, FinalAnswerTool
from .tool_types import ToolCallChunk
# Registry: name → async callable
TOOL_REGISTRY: dict[str, Tool] = {}

import logging
logger = logging.getLogger(__name__)

async def execute_tool(tc: ToolCallChunk, registry: Dict[str, Tool] = TOOL_REGISTRY) -> Dict[str, str]:
    """Returns the tool result as a JSON string."""
    tool = registry.get(tc.name)
    logger.info(f"Tool {tc.name} found in registry: {tool}")
    if not tool:
        return {"id":tc.id, "error": f"Unknown tool: {tc.name}"}
    try:
        args = json.loads(tc.arguments) if tc.arguments.strip() else {}
        logger.info(f"Executing tool {tc.name} with arguments {args}")
        # smolagents Tool.forward() is synchronous – run in executor so the
        # async contract of execute_tool is preserved without blocking the loop.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: tool.forward(**args))
        logger.info(f"Tool {tc.name} returned result: {result}")
        return {"id": tc.id, "result": result}
    except Exception as e:
        return {"id": tc.id, "error": str(e)}

# Uses this command to test:
# docker exec -it fastapi python3 utils/tools/executor.py
# ── Test Helpers ───────────────────────────────────────────────────────────────────
 
def make_registry(*tools: Tool) -> Dict[str, Tool]:
    return {t.name: t for t in tools}
 
 
def pass_(label: str, result: dict) -> None:
    print(f"  ✅  PASS  [{label}]")
    preview = str(result)[:120]
    print(f"       result: {preview}{'…' if len(str(result)) > 120 else ''}\n")
 
 
def fail_(label: str, result: dict, reason: str = "") -> None:
    print(f"  ❌  FAIL  [{label}]  {reason}")
    print(f"       result: {result}\n")
 
 
# ── Test cases ────────────────────────────────────────────────────────────────
 
async def test_unknown_tool() -> None:
    """Calling a tool that is not in the registry returns a descriptive error."""
    print("── test_unknown_tool ──")
    tc: ToolCallChunk = {"id": "tc-001", "name": "nonexistent_tool", "arguments": "{}"}
    result = await execute_tool(tc, registry={})
 
    if result.get("id") == "tc-001" and "error" in result and "nonexistent_tool" in result["error"]:
        pass_("unknown tool → error dict", result)
    else:
        fail_("unknown tool → error dict", result, "Expected id + error mentioning tool name")
 
 
async def test_bad_json_arguments() -> None:
    """Malformed JSON in arguments must be caught and returned as an error."""
    print("── test_bad_json_arguments ──")
    python_tool = PythonInterpreterTool()
    registry = make_registry(python_tool)
 
    tc: ToolCallChunk = {
        "id": "tc-002",
        "name": python_tool.name,
        "arguments": "NOT_VALID_JSON",
    }
    result = await execute_tool(tc, registry=registry)
 
    if result.get("id") == "tc-002" and "error" in result:
        pass_("bad JSON → error dict", result)
    else:
        fail_("bad JSON → error dict", result, "Expected id + error key")
 
 
async def test_python_interpreter_tool() -> None:
    """PythonInterpreterTool executes valid Python and returns the output."""
    print("── test_python_interpreter_tool ──")
    python_tool = PythonInterpreterTool()
    registry = make_registry(python_tool)
 
    code = "result = sum(range(1, 11))\nprint(result)"
    tc: ToolCallChunk = {
        "id": "tc-003",
        "name": python_tool.name,
        "arguments": json.dumps({"code": code}),
    }
    result = await execute_tool(tc, registry=registry)
 
    if result.get("id") == "tc-003" and "result" in result and "55" in str(result["result"]):
        pass_("PythonInterpreterTool → correct sum", result)
    else:
        fail_("PythonInterpreterTool → correct sum", result, "Expected '55' in result")
 
 
async def test_python_interpreter_runtime_error() -> None:
    """Runtime errors inside the tool are caught and surfaced as errors."""
    print("── test_python_interpreter_runtime_error ──")
    python_tool = PythonInterpreterTool()
    registry = make_registry(python_tool)
 
    # division by zero will raise inside the sandboxed interpreter
    tc: ToolCallChunk = {
        "id": "tc-004",
        "name": python_tool.name,
        "arguments": json.dumps({"code": "print(1 / 0)"}),
    }
    result = await execute_tool(tc, registry=registry)
 
    # smolagents may surface the error in "result" text OR raise → "error" key
    has_error_key = "error" in result
    has_error_in_result = "error" in str(result.get("result", "")).lower() or \
                          "ZeroDivision" in str(result.get("result", ""))
 
    if result.get("id") == "tc-004" and (has_error_key or has_error_in_result):
        pass_("PythonInterpreterTool runtime error handled", result)
    else:
        fail_("PythonInterpreterTool runtime error handled", result,
              "Expected id + some form of error indication")

async def test_python_interpreter_string_output() -> None:
    """PythonInterpreterTool correctly returns string manipulation results."""
    print("── test_python_interpreter_string_output ──")
    python_tool = PythonInterpreterTool()
    registry = make_registry(python_tool)
 
    tc: ToolCallChunk = {
        "id": "tc-005",
        "name": python_tool.name,
        "arguments": json.dumps({"code": "print('hello world'.upper())"}),
    }
    result = await execute_tool(tc, registry=registry)
 
    if result.get("id") == "tc-005" and "HELLO WORLD" in str(result.get("result", "")):
        pass_("PythonInterpreterTool → string output correct", result)
    else:
        fail_("PythonInterpreterTool → string output", result, "Expected 'HELLO WORLD'")
 
 
async def test_final_answer_tool_string() -> None:
    """FinalAnswerTool echoes back the answer it receives."""
    print("── test_final_answer_tool_string ──")
    final_tool = FinalAnswerTool()
    registry = make_registry(final_tool)
 
    tc: ToolCallChunk = {
        "id": "tc-006",
        "name": final_tool.name,
        "arguments": json.dumps({"answer": "42 is the answer"}),
    }
    result = await execute_tool(tc, registry=registry)
 
    if result.get("id") == "tc-006" and "42 is the answer" in str(result.get("result", "")):
        pass_("FinalAnswerTool → answer echoed back", result)
    else:
        fail_("FinalAnswerTool → answer echoed back", result,
              "Expected the answer string in result")
 
 
async def test_final_answer_tool_numeric() -> None:
    """FinalAnswerTool accepts numeric answers (the 'any' type input)."""
    print("── test_final_answer_tool_numeric ──")
    final_tool = FinalAnswerTool()
    registry = make_registry(final_tool)
 
    tc: ToolCallChunk = {
        "id": "tc-007",
        "name": final_tool.name,
        "arguments": json.dumps({"answer": 3.14}),
    }
    result = await execute_tool(tc, registry=registry)
 
    if result.get("id") == "tc-007" and "result" in result and "error" not in result:
        pass_("FinalAnswerTool → numeric answer accepted", result)
    else:
        fail_("FinalAnswerTool → numeric answer accepted", result,
              "Expected a clean result with no error")
 
 
async def test_multiple_tools_in_registry() -> None:
    """Registry can hold multiple tools; each is dispatched by name correctly."""
    print("── test_multiple_tools_in_registry ──")
    python_tool = PythonInterpreterTool()
    final_tool  = FinalAnswerTool()
    registry    = make_registry(python_tool, final_tool)
 
    tc_py: ToolCallChunk = {
        "id": "tc-008a",
        "name": python_tool.name,
        "arguments": json.dumps({"code": "print(7 * 6)"}),
    }
    res_py = await execute_tool(tc_py, registry=registry)
 
    tc_fa: ToolCallChunk = {
        "id": "tc-008b",
        "name": final_tool.name,
        "arguments": json.dumps({"answer": "done"}),
    }
    res_fa = await execute_tool(tc_fa, registry=registry)
 
    py_ok = res_py.get("id") == "tc-008a" and "42" in str(res_py.get("result", ""))
    fa_ok = res_fa.get("id") == "tc-008b" and "done" in str(res_fa.get("result", ""))
 
    if py_ok and fa_ok:
        pass_("multi-tool registry dispatches correctly", {"python": res_py, "final": res_fa})
    else:
        fail_("multi-tool registry dispatches correctly",
              {"python": res_py, "final": res_fa},
              f"py_ok={py_ok}, fa_ok={fa_ok}")
 
 
async def test_id_always_echoed() -> None:
    """The id from ToolCallChunk is always present in the response dict."""
    print("── test_id_always_echoed ──")
    final_tool = FinalAnswerTool()
    registry   = make_registry(final_tool)
 
    unique_id = "req-unique-xyz-999"
    tc: ToolCallChunk = {
        "id": unique_id,
        "name": final_tool.name,
        "arguments": json.dumps({"answer": "check"}),
    }
    result = await execute_tool(tc, registry=registry)
 
    if result.get("id") == unique_id:
        pass_("id always echoed in response", result)
    else:
        fail_("id always echoed in response", result, f"Expected id={unique_id!r}")
 
 
 
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
async def main() -> None:
    print("=" * 60)
    print("  execute_tool() — integration tests with smolagents")
    print("=" * 60)
    print()
 
    await test_unknown_tool()
    await test_bad_json_arguments()
    await test_python_interpreter_tool()
    await test_python_interpreter_runtime_error()
    await test_python_interpreter_string_output()
    await test_final_answer_tool_string()
    await test_final_answer_tool_numeric()
    await test_multiple_tools_in_registry()
    await test_id_always_echoed()
 
    print("=" * 60)
    print("  All tests completed.")
    print("=" * 60)
 
 
if __name__ == "__main__":
    asyncio.run(main())