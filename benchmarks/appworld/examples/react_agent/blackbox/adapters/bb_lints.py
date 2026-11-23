"""Candidate lints for the blackbox approach: the candidate is one store skill, ``SKILL.md`` plus the
tools capability in ``repl/helpers.py`` and ``repl/api_notes.json``, and the frozen
``scripts/init_repl.py``.

    python bb_lints.py CANDIDATE_DIR    # prints {"ok": bool, "violations": [...]}; exit 1 on violation,
                                        # exit 2 when the lint data is unavailable

A violating candidate is rejected before any episode runs (scored 0 with the violations as
feedback), as in the direct approach. Reused from the direct approach's ``lints.py`` (copied into the
project by scripts/setup_project.sh): the lint cache, the 8-gram / supervisor-contact leak check
(AppWorld's rule against task-specific hints) and the token counter.

Skill-folder rules (README: Lints):
- No skill-package additions (``scripts/``, ``references/``, ``assets/``) besides the frozen
  ``scripts/init_repl.py``, and nothing in ``repl/`` but ``helpers.py`` and ``api_notes.json``: the
  store would import any other file as a tool or an injected text.
- ``scripts/init_repl.py`` is FROZEN (``protected_paths`` in capevolve.yaml): present and
  byte-identical to the benchmark's copy. It is the template of the one store tool; the deployed tool
  is generated from the benchmark's copy, never from the candidate's. Other files in the candidate
  folder are the harness's own (INSTRUCTIONS.md, guidance/, JOURNAL.md, ...) and are never deployed.
- ``repl/helpers.py`` and ``repl/api_notes.json`` follow the direct approach's rules for
  ``tools/helpers.py`` and ``tools/api_notes.json`` (``lints.lint_helpers`` / ``lint_notes``).
- Nothing is required of SKILL.md about ``repl/``: as in the direct approach, the sentence listing the
  helpers is generated (the tailoring appends it to the deployed SKILL.md, the system prompt).

SKILL.md rules:
- Frontmatter ``name: my_skill``.
- The body is a Jinja template whose ONLY variable is ``main_user``, with only the fields
  first_name, last_name, email and phone_number. Every placeholder of the seed stays (each field
  at least once); no other ``{{ }}`` expression, no ``{% %}`` block, no filter. No
  ``{{ instruction }}``: the real task turn comes from AppWorld's client, after the proxy's marker.
- The app list rendered into the seed (``$AW_EVAL/app_descriptions.json``) stays verbatim: in the
  direct approach the optimizer only ever sees its ``{{ app_descriptions }}`` placeholder.
- The demo keeps its ``USER:`` / ``ASSISTANT:`` turns, and the body stays under the token limit.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lints as direct_lints  # noqa: E402

SKILL_NAME = "my_skill"
PACKAGE_DIRS = ("scripts", "references", "assets")  # skill-package parts the store would import
REPL_DIR = "repl"
REPL_FILES = ("helpers.py", "api_notes.json")
INIT_REPL_REL = "scripts/init_repl.py"  # frozen: protected_paths in capevolve.yaml
SKILL_MAX_TOKENS = 8500  # the direct prompt's 8,000 plus the rendered app list (~350 tokens)
MAIN_USER_FIELDS = {"first_name", "last_name", "email", "phone_number"}
PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
MAIN_USER_RE = re.compile(r"^\s*main_user\.([a-z_]+)\s*$")
WHAT = "SKILL.md"


def app_descriptions_path() -> Path:
    return Path(os.environ.get("AW_APP_DESCRIPTIONS") or direct_lints._eval_dir() / "app_descriptions.json")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``(frontmatter key -> value, body)``; an empty dict when there is no frontmatter."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[end + 5:]


def lint_skill_md(text: str) -> list[str]:
    out = []
    fm, body = split_frontmatter(text)
    if fm.get("name") != SKILL_NAME:
        out.append(f"{WHAT}: frontmatter must start the file and set name: {SKILL_NAME}")
    if "{%" in body or "{#" in body:
        out.append(f"{WHAT}: no Jinja {{% %}} blocks or {{# #}} comments; only the main_user placeholders")
    fields_used: set[str] = set()
    for expr in PLACEHOLDER_RE.findall(body):
        m = MAIN_USER_RE.match(expr)
        if not m:
            out.append(f"{WHAT}: placeholder {{{{{expr.strip()}}}}} is not allowed; only "
                       f"{{{{ main_user.<field> }}}} with field in {sorted(MAIN_USER_FIELDS)}")
        elif m.group(1) not in MAIN_USER_FIELDS:
            out.append(f"{WHAT}: main_user.{m.group(1)} does not exist; fields are {sorted(MAIN_USER_FIELDS)}")
        else:
            fields_used.add(m.group(1))
    missing = MAIN_USER_FIELDS - fields_used
    if missing:
        out.append(f"{WHAT}: the seed's placeholders must stay; missing main_user.{', main_user.'.join(sorted(missing))}")
    try:
        from jinja2 import Environment
        Environment().parse(body)
    except Exception as e:  # noqa: BLE001
        out.append(f"{WHAT}: Jinja template does not parse: {e}")
    if "USER:" not in body or "ASSISTANT:" not in body:
        out.append(f"{WHAT}: the few-shot demo must keep its USER: / ASSISTANT: turns")
    apps_file = app_descriptions_path()
    if apps_file.is_file():
        apps = json.loads(apps_file.read_text())["text"]
        if apps not in body:
            out.append(f"{WHAT}: the app list rendered into the seed must stay verbatim (it is AppWorld's "
                       "app list, not part of the capability)")
    else:
        out.append(f"{WHAT}: app list unavailable ({apps_file} missing; scripts/setup_project.sh writes it)")
    n = direct_lints._tokens(body)
    if n > SKILL_MAX_TOKENS:
        out.append(f"{WHAT}: {n} tokens > {SKILL_MAX_TOKENS}")
    if apps_file.is_file():
        body = body.replace(json.loads(apps_file.read_text())["text"], "")  # AppWorld's own text, not the candidate's
    return out + direct_lints._leaks(body, WHAT)


def _relabel(violations: list[str]) -> list[str]:
    """The direct lints name their files tools/...; here they live in repl/."""
    return [v.replace("tools/helpers.py", f"{REPL_DIR}/helpers.py")
             .replace("tools/api_notes.json", f"{REPL_DIR}/api_notes.json") for v in violations]


def lint_repl(cdir: Path) -> list[str]:
    """The tools capability: the two repl/ files, under the direct approach's rules."""
    d = Path(cdir) / REPL_DIR
    out = []
    helpers = (d / "helpers.py").read_text(encoding="utf-8") if (d / "helpers.py").is_file() else ""
    notes_raw = (d / "api_notes.json").read_text(encoding="utf-8") if (d / "api_notes.json").is_file() else ""
    if helpers:
        out += _relabel(direct_lints.lint_helpers(helpers))
    if notes_raw.strip():
        out += _relabel(direct_lints.lint_notes(notes_raw))
    return out


def frozen_init_repl() -> Path:
    """The benchmark's own copy of the frozen scripts/init_repl.py: the project's (next to this file),
    else the committed seed's (in the repo)."""
    here = Path(__file__).resolve().parent
    project = here / "init_repl_template.py"
    return project if project.is_file() else here.parent / "seed_capability" / SKILL_NAME / INIT_REPL_REL


def lint_init_repl(cdir: Path) -> list[str]:
    """scripts/init_repl.py is frozen: present and byte-identical to the benchmark's copy."""
    mine = Path(cdir) / INIT_REPL_REL
    if not mine.is_file():
        return [f"{INIT_REPL_REL}: missing; it is frozen (protected_paths): restore it, do not delete it"]
    if mine.read_bytes() != frozen_init_repl().read_bytes():
        return [f"{INIT_REPL_REL}: changed; it is frozen (protected_paths): restore the original"]
    return []


def skill_dir(path: Path) -> Path:
    """The skill folder of a candidate: ``<candidate>/my_skill`` (capability_path: seed_capability,
    as in tau2), or ``path`` itself when it already is the skill folder."""
    path = Path(path)
    return path / SKILL_NAME if (path / SKILL_NAME / "SKILL.md").is_file() else path


def lint_candidate(cdir: Path) -> list[str]:
    cdir = skill_dir(cdir)
    skill = cdir / "SKILL.md"
    if not skill.is_file():
        return [f"{WHAT}: missing"]
    out = []
    others = sorted(str(p.relative_to(cdir)) for sub in PACKAGE_DIRS for p in (cdir / sub).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                    and p.relative_to(cdir).as_posix() != INIT_REPL_REL)
    others += sorted(str(p.relative_to(cdir)) for p in (cdir / REPL_DIR).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts
                     and p.relative_to(cdir / REPL_DIR).as_posix() not in REPL_FILES)
    if others:
        out.append(f"the skill holds SKILL.md, {INIT_REPL_REL} (frozen) and "
                   f"{REPL_DIR}/{{{','.join(REPL_FILES)}}} only; found {others}")
    out += lint_init_repl(cdir)
    text = skill.read_text(encoding="utf-8")
    return out + lint_skill_md(text) + lint_repl(cdir)


if __name__ == "__main__":
    try:
        v = lint_candidate(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    except direct_lints.LintDataUnavailable as e:
        print(json.dumps({"ok": False, "violations": [str(e)]}, indent=1))
        raise SystemExit(2)
    print(json.dumps({"ok": not v, "violations": v}, indent=1))
    raise SystemExit(1 if v else 0)
