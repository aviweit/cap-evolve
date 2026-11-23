"""The blackbox seed skill: build the committed seed, and render it into a project at setup.

    $AW_EVAL/.venv/bin/python make_seed_skill.py SEED_SKILL_DIR OUT_SKILL_DIR APP_DESCRIPTIONS_JSON
        render the committed seed (../seed_capability/my_skill) into the project: copy it, filling
        {{ app_descriptions }} in SKILL.md (what scripts/setup_project.sh runs)
    python make_seed_skill.py --build STOCK_INSTRUCTIONS_TXT SEED_SKILL_DIR
        (re)write the committed seed from AppWorld's stock prompt (the direct seed's
        ../../seed_capability/policy/instructions.txt is that prompt, byte for byte)

The committed seed, ``seed_capability/my_skill/``:
- SKILL.md body: the stock prompt from its first line through the "**Key instructions**" turn, byte for
  byte: everything before the final "USER:\\nUsing these APIs, now generate code to solve the actual
  task:" turn. That turn is sent by AppWorld's client and kept by the proxy's history-start marker
  (adapters/appworld_tailoring.HISTORY_START_REGEX). A short header says the USER/ASSISTANT turns are a
  worked few-shot example.
- {{ app_descriptions }} stays a placeholder there, as in the direct seed: the rendered app list comes
  from AppWorld's installed app code and is never committed. At setup it is rendered exactly as
  AppWorld's agent renders it (all 11 apps, the same for every task); the rendered text also goes to
  APP_DESCRIPTIONS_JSON, which the lint uses to keep it verbatim. The proxy cannot fill it per request
  (multi-line JSON does not fit in a header).
- The {{ main_user.* }} slots stay in both: the proxy fills them per request from the
  skillberry-context-template-main_user-* headers.
- repl/helpers.py (a docstring only) and repl/api_notes.json ({}) load nothing, so the seed deploys
  no store tool and keeps the stock agent's behaviour, as the direct approach's empty tools/ does.
"""

import json
import shutil
import sys
from pathlib import Path

SKILL_NAME = "my_skill"
FINAL_TURN = "USER:\nUsing these APIs, now generate code to solve the actual task:"
APP_SLOT = "{{ app_descriptions }}"
HEADER = (
    "The USER/ASSISTANT turns below, up to and including the \"**Key instructions**\" turn, are a "
    "worked few-shot example of how to act and the rules to follow. They are not part of the current "
    "conversation. The real task follows after them.\n\n"
)


def build(stock_text: str, app_descriptions: str = APP_SLOT) -> str:
    """SKILL.md from the stock prompt; the default keeps the app list a placeholder (the committed seed)."""
    cut = stock_text.rfind(FINAL_TURN)
    if cut < 0:
        raise ValueError("final task turn not found in the stock prompt")
    body = stock_text[:cut].rstrip("\n") + "\n"
    if body.count(APP_SLOT) != 1:
        raise ValueError("expected exactly one {{ app_descriptions }} slot")
    body = body.replace(APP_SLOT, app_descriptions)
    return (f"---\nname: {SKILL_NAME}\ndescription: AppWorld ReAct policy and few-shot example "
            f"(cap-evolve system-prompt capability)\n---\n" + HEADER + body)


def render(skill_text: str, app_descriptions: str) -> str:
    """Fill the one app-list placeholder of the committed SKILL.md."""
    if skill_text.count(APP_SLOT) != 1:
        raise ValueError("the committed SKILL.md must hold exactly one {{ app_descriptions }} slot")
    return skill_text.replace(APP_SLOT, app_descriptions)


SEED_HELPERS = (
    '"""Helper functions loaded into the AppWorld REPL by the init_repl store tool (capability: tools).\n'
    "\n"
    "Empty in the seed. See optimizer/INSTRUCTIONS.md for the rules a helper must follow.\n"
    '"""\n'
)


def write_repl(out_dir: Path) -> None:
    """The seed's tools capability: nothing to load."""
    repl = out_dir / "repl"
    repl.mkdir(parents=True, exist_ok=True)
    (repl / "helpers.py").write_text(SEED_HELPERS, encoding="utf-8")
    (repl / "api_notes.json").write_text("{}\n", encoding="utf-8")


def render_app_descriptions() -> str:
    """As SimplifiedReActCodeAgent.initialize renders it, for the apps every task gets."""
    from appworld.apps import get_all_apps, get_app_to_description  # needs AppWorld ($AW_EVAL/.venv)

    descriptions = get_app_to_description()
    apps = get_all_apps(skip_admin=True)  # Task.load: allowed_apps
    return json.dumps([{"name": a, "description": descriptions[a]} for a in apps], indent=1)


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--build":
        stock, seed = Path(argv[2]), Path(argv[3])
        seed.mkdir(parents=True, exist_ok=True)
        (seed / "SKILL.md").write_text(build(stock.read_text(encoding="utf-8")), encoding="utf-8")
        write_repl(seed)
        print(f"wrote the committed seed {seed}")
        return 0
    seed, out_dir, apps_json = Path(argv[1]), Path(argv[2]), Path(argv[3])
    apps = render_app_descriptions()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(seed, out_dir, ignore=shutil.ignore_patterns("__pycache__"))
    text = render((seed / "SKILL.md").read_text(encoding="utf-8"), apps)
    (out_dir / "SKILL.md").write_text(text, encoding="utf-8")
    apps_json.write_text(json.dumps({"text": apps}), encoding="utf-8")
    print(f"wrote {out_dir} from {seed} ({len(text)} chars, {text.count('{{ main_user.')} main_user slots, "
          f"app list rendered) and {apps_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
