"""init_repl: the one store tool of the AppWorld blackbox approach (capability: tools).

FROZEN: the optimizer must not edit this file. It is in ``protected_paths`` (capevolve.yaml), so any
change is caught as tampering, and the lint rejects a candidate whose copy differs from the
benchmark's own. It is a TEMPLATE: at deploy time (``appworld_tailoring.build_init_repl``) the
candidate's ``repl/helpers.py`` and ``repl/api_notes.json`` are turned into the REPL code the direct
approach runs (``aw_agent.setup_snippet``) and written below as the literal ``_CODE``; the result is
staged as the skill's ``scripts/init_repl.py`` (and only when ``repl/`` loads anything). The store runs
it when the model calls the tool, with the request's Skillberry context as the global
``skillberry_context``: ``task_id`` and ``appworld.repl_port`` (the header
``skillberry-context-appworld-repl_port``) name the trial's own AppWorld environment server.

The code it loads only DEFINES things (helper functions, the two wrapped doc lookups), and running it
again restores the originals before re-wrapping them, so a second call changes nothing. The model
cannot see its earlier tool calls (the proxy keeps them inside one request), so this matters.
Only ``/execute`` is called: the store never reaches the world's ``/evaluate``.
"""

import json
import re
import urllib.request

_CODE = "__AW_REPL_CODE__"
_HELPERS = "__AW_REPL_HELPERS__"
_HAS_NOTES = "__AW_REPL_HAS_NOTES__"
# _CODE prints this marker with how many times it has run in this world (appworld_tailoring.repl_code)
_LOADS = re.compile(r"aw-repl-loads: (\d+)")
# Every answer ends the tool step: a weak model otherwise calls the tool again and again in one turn.
_NEXT = "Do not call init_repl again in this task. Reply now with your one ```python code block."


def init_repl() -> str:
    """Load the pre-defined helper functions and API notes into this task's Python REPL.

    Call it once, at the very start of the task, never again. Then reply with your code.

    Returns:
        str: what is now available in the REPL, or why loading failed.
    """
    try:
        ctx = skillberry_context  # noqa: F821  (set by the store for every tool call)
    except NameError:
        ctx = {}
    port = (ctx.get("appworld") or {}).get("repl_port")
    task_id = ctx.get("task_id")
    if not port or not task_id or not str(port).isdigit():
        return "init_repl: no REPL is attached to this conversation."
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/execute",
        data=json.dumps({"task_id": str(task_id), "code": _CODE}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            output = str(json.loads(resp.read()).get("output", ""))
    except Exception as e:  # noqa: BLE001
        return f"init_repl: the REPL did not answer ({type(e).__name__})."
    if "Traceback" in output or "Execution failed" in output:
        return "init_repl: loading failed:\n" + output[-1500:]
    parts = []
    if _HELPERS:
        parts.append("helper functions " + ", ".join(_HELPERS) + " are defined (see help(name))")
    if _HAS_NOTES:
        parts.append("apis.api_docs.show_api_doc / show_api_descriptions now add a 'note' field where one exists")
    what = "; ".join(parts) or "nothing to load"
    loads = _LOADS.search(output)
    if loads and int(loads.group(1)) > 1:
        return f"Already loaded, nothing changed: {what}. {_NEXT}"
    return f"REPL ready: {what}. {_NEXT}"
