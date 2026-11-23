"""Offline tests for the AppWorld blackbox approach: tailoring, the seed skill, the SKILL.md lints and
the adapter's deploy/lint wiring. No AppWorld install, no store, no proxy and no model needed.

    python3 benchmarks/appworld/examples/react_agent/blackbox/tests/test_blackbox.py -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

EX = Path(__file__).resolve().parents[1]
DIRECT = EX.parent  # the direct example is the parent folder
sys.path.insert(0, str(EX / "adapters"))
sys.path.insert(0, str(EX / "scripts"))
sys.path.insert(1, str(DIRECT / "adapters"))  # the direct harness the blackbox adapter reuses
sys.path.append(str(Path(__file__).resolve().parents[6] / "core"))

import appworld_tailoring as tailoring  # noqa: E402
import bb_lints  # noqa: E402
import lints as direct_lints  # noqa: E402
import make_seed_skill  # noqa: E402


def load_bb_adapter():
    """The blackbox adapter by file path: the direct tests also import a module named `adapter`."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("aw_blackbox_adapter", EX / "adapters" / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

STOCK = (DIRECT / "seed_capability" / "policy" / "instructions.txt").read_text(encoding="utf-8")
SEED_SKILL = EX / "seed_capability" / "my_skill"  # the committed blackbox seed
APPS = json.dumps([{"name": "spotify", "description": "A music app."}], indent=1)
SUPERVISOR = {"first_name": "Joseph", "last_name": "Weber", "email": "joseph.webe@gmail.com",
              "phone_number": "2233697879"}


class LintDataMixin:
    """A fake lint cache (no 8-grams, no contacts) and app-list file, so lints run offline."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cache = self.tmp / "lint_cache.json"
        cache.write_text(json.dumps({"grams": [], "contacts": ["timothy white"], "apps": ["spotify"],
                                     "api_names": ["login"]}))
        apps = self.tmp / "app_descriptions.json"
        apps.write_text(json.dumps({"text": APPS}))
        self.env = patch.dict(os.environ, {"AW_LINT_CACHE": str(cache), "AW_APP_DESCRIPTIONS": str(apps)})
        self.env.start()
        direct_lints.lint_data.cache_clear()

    def tearDown(self):
        self.env.stop()
        direct_lints.lint_data.cache_clear()

    def seed_dir(self) -> Path:
        """A rendered seed skill folder, as setup_project.sh writes it (SKILL.md + the frozen tool)."""
        d = self.tmp / "my_skill"
        (d / "scripts").mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(make_seed_skill.build(STOCK, APPS), encoding="utf-8")
        (d / "scripts" / "init_repl.py").write_bytes(SEED_SKILL.joinpath("scripts", "init_repl.py").read_bytes())
        return d


class TestTailoring(unittest.TestCase):
    def test_marker_matches_only_the_real_task_turn(self):
        rx = re.compile(tailoring.HISTORY_START_REGEX)
        turns = re.split(r"^(?:USER|ASSISTANT):\n", STOCK, flags=re.M)
        hits = [t for t in turns if rx.search(t)]
        self.assertEqual(len(hits), 1)
        self.assertIn("Task: {{ instruction }}", hits[0])
        self.assertIsNone(rx.search("Using these APIs, you can now generate code, that I will execute"))

    def test_context_headers(self):
        h = tailoring.context_headers("aw-ep1", "07b42fd_2", SUPERVISOR)
        self.assertEqual(h["skillberry-context-env_id"], "aw-ep1")
        self.assertEqual(h["skillberry-context-task_id"], "07b42fd_2")
        self.assertEqual(h["skillberry-context-plugin"], "appworld")
        self.assertEqual(h["skillberry-context-history_start_regex"], tailoring.HISTORY_START_REGEX)
        for f in tailoring.SUPERVISOR_FIELDS:
            self.assertEqual(h[f"skillberry-context-template-main_user-{f}"], SUPERVISOR[f])

    def test_header_values_must_be_single_line_ascii(self):
        with self.assertRaises(ValueError):
            tailoring.context_headers("e", "t", dict(SUPERVISOR, last_name="Line\nbreak"))
        with self.assertRaises(ValueError):
            tailoring.context_headers("e", "t", dict(SUPERVISOR, first_name="José"))

    def test_spa_model_name_mapping(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AW_SPA_MODEL", None)
            self.assertEqual(tailoring.spa_model_name("hosted_vllm/openai/gpt-oss-120b-a100"),
                             "openai/openai/gpt-oss-120b-a100")
            self.assertEqual(tailoring.spa_model_name("litellm_proxy/aws/claude-haiku-4-5"), "openai/aws/claude-haiku-4-5")
            self.assertEqual(tailoring.spa_model_name("openai/aws/gpt-oss-120b"), "openai/aws/gpt-oss-120b")
        with patch.dict(os.environ, {"AW_SPA_MODEL": "openai/custom"}):
            self.assertEqual(tailoring.spa_model_name("anything"), "openai/custom")

    def test_spa_upstream_env_requires_bearer(self):
        with self.assertRaises(tailoring.BlackboxConfigError):
            tailoring.spa_upstream_env({"AW_KEY_HEADER": "RITS_API_KEY"})

    def test_spa_upstream_env_reads_only_the_key_and_url_from_the_secrets_file(self):
        with tempfile.TemporaryDirectory() as d:
            secrets = Path(d) / ".env"
            secrets.write_text("GW_KEY=sk-test\nMODEL_SERVER_URL=http://gw.example\nOTHER=nope\n")
            env = tailoring.spa_upstream_env({
                "AW_KEY_HEADER": "bearer", "AW_KEY_ENV": "GW_KEY", "AW_SECRETS_FILE": str(secrets),
                "AW_MODEL_NAME": "litellm_proxy/aws/gpt-oss-120b", "AW_MODEL_URL": "{{MODEL_SERVER_URL}}/v1"})
        self.assertEqual(env, {"SPA_MODEL_NAME": "openai/aws/gpt-oss-120b", "OPENAI_API_BASE": "http://gw.example/v1",
                               "OPENAI_BASE_URL": "http://gw.example/v1", "OPENAI_API_KEY": "sk-test"})

    def test_deploy_is_once_per_candidate_content(self):
        calls = []

        class FakeBB:
            Protection = staticmethod(lambda: "protect")

            @staticmethod
            def start_store():
                calls.append("start_store")

            @staticmethod
            def reset_store_to_skill(d, name, protect):
                calls.append(("reset", name))

            @staticmethod
            def restart_spa(name, **env):
                calls.append(("restart", name, env["SPA_MODEL_NAME"]))

        with tempfile.TemporaryDirectory() as d, \
                patch.object(tailoring, "blackbox_env", lambda: FakeBB), \
                patch.object(tailoring, "spa_upstream_env", lambda: {"SPA_MODEL_NAME": "m"}):
            tailoring._deployed["digest"] = None
            skill = Path(d)
            (skill / "SKILL.md").write_text("v1")
            self.assertTrue(tailoring.deploy(skill))
            self.assertFalse(tailoring.deploy(skill))  # same content: no restart
            (skill / "SKILL.md").write_text("v2")
            self.assertTrue(tailoring.deploy(skill))
        self.assertEqual([c for c in calls if c[0] == "restart"], [("restart", "my_skill", "m")] * 2)


class TestSeedSkill(unittest.TestCase):
    def test_body_is_the_stock_prompt_up_to_the_real_task_turn(self):
        text = make_seed_skill.build(STOCK, APPS)
        fm, body = bb_lints.split_frontmatter(text)
        self.assertEqual(fm["name"], "my_skill")
        self.assertTrue(body.startswith(make_seed_skill.HEADER))
        cut = STOCK.rfind(make_seed_skill.FINAL_TURN)
        expected = STOCK[:cut].rstrip("\n").replace("{{ app_descriptions }}", APPS) + "\n"
        self.assertEqual(body[len(make_seed_skill.HEADER):], expected)  # byte for byte
        self.assertNotIn("now generate code to solve the actual task", body)
        self.assertNotIn("{{ instruction }}", body)
        self.assertEqual(body.count("{{ main_user."), 6)


class TestSkillLints(LintDataMixin, unittest.TestCase):
    def test_seed_passes(self):
        self.assertEqual(bb_lints.lint_candidate(self.seed_dir()), [])

    def edited(self, old: str, new: str) -> list[str]:
        d = self.seed_dir()
        text = (d / "SKILL.md").read_text()
        self.assertIn(old, text)
        (d / "SKILL.md").write_text(text.replace(old, new, 1))
        return bb_lints.lint_candidate(d)

    def test_removed_placeholder_is_rejected(self):
        v = self.edited("{{ main_user.phone_number }}", "the phone number")
        self.assertTrue(any("main_user.phone_number" in x for x in v), v)

    def test_invented_field_is_rejected(self):
        self.assertTrue(any("main_user.address does not exist" in x
                            for x in self.edited("{{ main_user.email }}", "{{ main_user.address }}")))

    def test_instruction_placeholder_is_rejected(self):
        self.assertTrue(self.edited("**Key instructions**", "{{ instruction }} **Key instructions**"))

    def test_jinja_block_is_rejected(self):
        self.assertTrue(any("blocks" in x for x in self.edited("**Key instructions**", "{% if x %}y{% endif %}**Key instructions**")))

    def test_app_list_must_stay_verbatim(self):
        self.assertTrue(any("app list" in x for x in self.edited("A music app.", "A great music app.")))

    def test_frontmatter_name(self):
        self.assertTrue(any("name: my_skill" in x for x in self.edited("name: my_skill", "name: other")))

    def test_skill_package_addition_is_rejected(self):
        d = self.seed_dir()
        (d / "scripts").mkdir(exist_ok=True)
        (d / "scripts" / "x.py").write_text("def x():\n    return 1\n")
        self.assertTrue(any("found ['scripts/x.py']" in x for x in bb_lints.lint_candidate(d)))

    def test_harness_files_are_ignored(self):
        d = self.seed_dir()
        for rel in ("INSTRUCTIONS.md", "JOURNAL.md", "guidance/sources/api_index.md"):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text("harness file")
        self.assertEqual(bb_lints.lint_candidate(d), [])

    def test_supervisor_contact_is_rejected(self):
        self.assertTrue(any("supervisor" in x for x in self.edited("**Key instructions**", "Timothy White **Key instructions**")))

    def test_missing_skill(self):
        self.assertEqual(bb_lints.lint_candidate(self.tmp), ["SKILL.md: missing"])


class TestAdapterWiring(LintDataMixin, unittest.TestCase):
    def test_lint_reject_runs_no_episode_and_does_not_deploy(self):
        bb_adapter = load_bb_adapter()
        d = self.seed_dir()
        (d / "scripts").mkdir(exist_ok=True)
        (d / "scripts" / "x.py").write_text("def x():\n    return 1\n")
        task = bb_adapter.Task(id="t1", input={})
        with patch.object(bb_adapter.Adapter, "_check_seal", lambda self, ids: None), \
                patch.object(bb_adapter, "_wait_readable", lambda p, timeout_s=60.0: None), \
                patch.object(bb_adapter.tailoring, "deploy", side_effect=AssertionError("deployed")):
            out = bb_adapter.Adapter().run_trials([task], d, n_trials=2, base_seed=0)
        self.assertEqual([r.output for r in out["t1"]], ["lint_reject", "lint_reject"])

    def test_deploy_failure_is_infra_not_candidate(self):
        bb_adapter = load_bb_adapter()
        task = bb_adapter.Task(id="t1", input={})
        with patch.object(bb_adapter.Adapter, "_check_seal", lambda self, ids: None), \
                patch.object(bb_adapter, "_wait_readable", lambda p, timeout_s=60.0: None), \
                patch.object(bb_adapter.tailoring, "deploy", side_effect=RuntimeError("store down")):
            out = bb_adapter.Adapter().run_trials([task], self.seed_dir(), n_trials=1, base_seed=0)
        self.assertTrue(out["t1"][0].error.startswith("infrastructure:"))


class TestNativeSims(unittest.TestCase):
    """Each episode's own ReAct message list lands in <run_dir>/native_sims/<tag>/<split>/, and never
    in the rollout."""

    def setUp(self):
        self.bb = load_bb_adapter()
        self.run = Path(tempfile.mkdtemp())
        (self.run / "splits.json").write_text(json.dumps({"train": ["a", "b"], "val": ["a", "b"], "test": ["c"]}))
        for d in ("work/cand_0001", "candidates/seed"):
            (self.run / d).mkdir(parents=True)

    def test_path_is_run_dir_native_sims_tag_split(self):
        self.assertEqual(self.bb._native_dir(self.run / "work" / "cand_0001", ["a", "b"]),
                         self.run / "native_sims" / "cand_0001" / "val")
        self.assertEqual(self.bb._native_dir(self.run / "candidates" / "seed", ["c"]),
                         self.run / "native_sims" / "seed" / "test")

    def test_off_and_unknown_layout_write_nothing(self):
        with patch.dict(os.environ, {"CAPEVOLVE_NATIVE_SIMS": "0"}):
            self.assertIsNone(self.bb._native_dir(self.run / "work" / "cand_0001", ["a"]))
        self.assertIsNone(self.bb._native_dir(self.run / "elsewhere", ["a"]))

    def test_a_second_record_never_replaces_the_first(self):
        dest = self.run / "native_sims" / "seed" / "val" / "a__t0.json"
        first = self.bb._write_native(dest, {"phase": "baseline"})
        second = self.bb._write_native(dest, {"phase": "final"})
        self.assertEqual((first, second), (dest, dest.with_name("a__t0__2.json")))
        self.assertEqual(json.loads(dest.read_text())["phase"], "baseline")
        self.assertEqual(json.loads(second.read_text())["phase"], "final")

    def test_episode_writes_messages_to_native_sims_not_the_rollout(self):
        native_msgs = [{"role": "user", "content": "stock prompt"}, {"role": "assistant", "content": "code"}]

        def fake_run(argv, **kw):
            Path(argv[3]).write_text(json.dumps({
                "task_id": "a", "termination": "completed", "steps": [{"step": 1}],
                "native": {"task_id": "a", "num_prompt_messages": 1, "messages": native_msgs}}))
            return type("P", (), {"returncode": 0, "stderr": ""})()

        dest = self.run / "native_sims" / "cand_0001" / "val" / "a__t0.json"
        with patch.object(self.bb, "_supervisor", lambda tid, root: SUPERVISOR), \
                patch.object(self.bb, "_client_prompt", lambda: Path("instructions.txt")), \
                patch.object(self.bb.subprocess, "run", fake_run), \
                patch.dict(os.environ, {"AW_REMOTE_REPL": "0"}), \
                patch.object(self.bb.tailoring, "disconnect", lambda *a, **k: True):
            r = self.bb.Adapter()._episode(self.bb.Task(id="a", input={}), 0, self.run, self.run, dest)
        self.assertNotIn("native", r.metadata)
        self.assertEqual(r.trace, [{"step": 1}])
        saved = json.loads(dest.read_text())
        self.assertEqual(saved["messages"], native_msgs)
        self.assertEqual((saved["seed"], saved["termination"]), (0, "completed"))


class TestDisconnectOnTrialEnd(unittest.TestCase):
    """The proxy's per-episode state is released when the trial ends, however it ended."""

    def run_episode(self, fake_run):
        bb = load_bb_adapter()
        calls = []
        run = Path(tempfile.mkdtemp())
        with patch.object(bb, "_supervisor", lambda tid, root: SUPERVISOR), \
                patch.object(bb, "_client_prompt", lambda: Path("instructions.txt")), \
                patch.object(bb.subprocess, "run", fake_run), \
                patch.dict(os.environ, {"AW_INFRA_RETRIES": "0", "AW_REMOTE_REPL": "0"}), \
                patch.object(bb.tailoring, "disconnect", lambda headers, **k: calls.append(headers) or True):
            r = bb.Adapter()._episode(bb.Task(id="a", input={}), 0, run, run)
        return r, calls

    def test_worker_killed_at_the_wall_cap_is_disconnected_by_the_adapter(self):
        def fake_run(argv, **kw):
            raise __import__("subprocess").TimeoutExpired(argv, 1)
        r, calls = self.run_episode(fake_run)
        self.assertTrue(r.error.startswith("infrastructure:"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["skillberry-context-env_id"].startswith("aw-a__s0__"))

    def test_worker_that_disconnected_itself_is_not_disconnected_again(self):
        def fake_run(argv, **kw):
            Path(argv[3]).write_text(json.dumps({"task_id": "a", "termination": "completed", "steps": [],
                                                 "disconnected": True}))
            return type("P", (), {"returncode": 0, "stderr": ""})()
        r, calls = self.run_episode(fake_run)
        self.assertIsNone(r.error)
        self.assertEqual(calls, [])


class TestRemoteRepl(unittest.TestCase):
    """Each trial gets its own AppWorld environment server; the adapter owns its port and lifetime."""

    def test_ports_are_unique_and_skip_ports_already_bound(self):
        import socket
        env = {"AW_ENV_PORTS": "9390-9399"}
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 9390))
            busy.listen(1)
            got = [tailoring.reserve_port(env) for _ in range(3)]
        try:
            self.assertNotIn(9390, got)
            self.assertEqual(len(set(got)), 3)
        finally:
            for p in got:
                tailoring.release_port(p)

    def test_port_range_runs_out(self):
        env = {"AW_ENV_PORTS": "9398-9398"}
        p = tailoring.reserve_port(env)
        try:
            with self.assertRaises(RuntimeError):
                tailoring.reserve_port(env)
        finally:
            tailoring.release_port(p)

    def test_off_switch(self):
        self.assertTrue(tailoring.remote_repl_enabled({}))
        self.assertFalse(tailoring.remote_repl_enabled({"AW_REMOTE_REPL": "0"}))

    def _episode(self, fake_run, start=lambda root, env: 9123):
        bb = load_bb_adapter()
        stopped, seen = [], {}
        run = Path(tempfile.mkdtemp())

        def run_and_capture(argv, **kw):
            seen.update(json.loads(Path(argv[2]).read_text()))
            return fake_run(argv, **kw)

        with patch.object(bb, "_supervisor", lambda tid, root: SUPERVISOR), \
                patch.object(bb, "_client_prompt", lambda: Path("instructions.txt")), \
                patch.object(bb.subprocess, "run", run_and_capture), \
                patch.dict(os.environ, {"AW_INFRA_RETRIES": "0", "AW_REMOTE_REPL": "1"}), \
                patch.object(bb.tailoring, "start_env_server", start), \
                patch.object(bb.tailoring, "stop_env_server", lambda port: stopped.append(port) or True), \
                patch.object(bb.tailoring, "disconnect", lambda *a, **k: True):
            r = bb.Adapter()._episode(bb.Task(id="a", input={}), 0, run, run)
        return r, seen, stopped

    def test_the_world_gets_the_trials_server_and_the_server_is_stopped(self):
        def fake_run(argv, **kw):
            Path(argv[3]).write_text(json.dumps({"task_id": "a", "termination": "completed", "steps": [],
                                                 "disconnected": True}))
            return type("P", (), {"returncode": 0, "stderr": ""})()
        r, seen, stopped = self._episode(fake_run)
        self.assertEqual(seen["remote_environment_url"], "http://127.0.0.1:9123")
        self.assertEqual(stopped, [9123])
        self.assertIsNone(r.error)

    def test_server_is_stopped_even_when_the_worker_is_killed(self):
        def fake_run(argv, **kw):
            raise __import__("subprocess").TimeoutExpired(argv, 1)
        r, _, stopped = self._episode(fake_run)
        self.assertTrue(r.error.startswith("infrastructure:"))
        self.assertEqual(stopped, [9123])

    def test_a_server_that_does_not_start_is_infra_and_runs_no_worker(self):
        def no_server(root, env):
            raise RuntimeError("did not come up")

        def fake_run(argv, **kw):
            raise AssertionError("the worker must not run without its server")
        r, seen, stopped = self._episode(fake_run, start=no_server)
        self.assertIn("remote REPL did not start", r.error)
        self.assertEqual((seen, stopped), ({}, []))


HELPERS = '''"""Helpers."""


def fetch_all_pages(apis, app_name, api_name, **kwargs):
    """Every page of a paginated API."""
    fn = getattr(getattr(apis, app_name), api_name)
    out, page = [], 0
    while True:
        batch = fn(page_index=page, **kwargs)
        if not batch:
            return out
        out += batch
        page += 1
'''
class ReplMixin(LintDataMixin):
    def repl_dir(self, helpers=HELPERS, notes=None, skill_line=None) -> Path:
        d = self.seed_dir()
        if skill_line:
            text = (d / "SKILL.md").read_text()
            (d / "SKILL.md").write_text(text.replace("**Key instructions**", skill_line + "\n**Key instructions**", 1))
        (d / "repl").mkdir(exist_ok=True)
        (d / "repl" / "helpers.py").write_text(helpers)
        (d / "repl" / "api_notes.json").write_text(json.dumps(notes or {}))
        return d


class TestReplLints(ReplMixin, unittest.TestCase):
    def test_seed_repl_passes_and_loads_nothing(self):
        d = self.seed_dir()
        make_seed_skill.write_repl(d)
        self.assertEqual(bb_lints.lint_candidate(d), [])
        self.assertEqual(tailoring.build_init_repl(d), "")

    def test_helpers_and_notes_pass_without_skill_md_mentioning_them(self):
        # As in direct: the sentence naming the helpers is generated, not the optimizer's job.
        d = self.repl_dir(notes={"spotify": "Paginated: pass page_index."})
        self.assertNotIn("fetch_all_pages", (d / "SKILL.md").read_text())
        self.assertEqual(bb_lints.lint_candidate(d), [])


class TestSystemPromptNotice(ReplMixin, unittest.TestCase):
    """The deployed SKILL.md (the system prompt) gets direct's generated helper sentence."""

    def test_the_direct_sentence_ends_the_deployed_skill_md(self):
        import aw_agent
        d = self.repl_dir()
        deployed = tailoring.deployed_skill_md(d)
        direct = aw_agent.helper_notice(HELPERS)
        self.assertTrue(direct.startswith("Pre-defined helper functions are available in the REPL "
                                          "(call them directly; see their docstrings with help(name)): "))
        self.assertIn("fetch_all_pages(apis, app_name, api_name)", direct)
        self.assertTrue(deployed.endswith(f"\n\n{direct} {tailoring.INIT_REPL_LINE}\n"))
        self.assertTrue(deployed.startswith((d / "SKILL.md").read_text().rstrip()))

    def test_notes_only_still_says_to_call_init_repl(self):
        d = self.repl_dir(helpers='"""doc only"""\n', notes={"spotify": "n"})
        self.assertTrue(tailoring.deployed_skill_md(d).endswith(f"\n\n{tailoring.INIT_REPL_NOTES_LINE}\n"))

    def test_seed_skill_md_is_deployed_unchanged(self):
        d = self.seed_dir()
        make_seed_skill.write_repl(d)
        self.assertEqual(tailoring.deployed_skill_md(d), (d / "SKILL.md").read_text())

    def test_deployed_skill_md_still_passes_the_skill_lint(self):
        # The proxy renders it as a Jinja template: the sentence must add no placeholder.
        self.assertEqual(bb_lints.lint_skill_md(tailoring.deployed_skill_md(self.repl_dir())), [])

    def test_direct_helper_rules_apply_under_repl(self):
        d = self.repl_dir(helpers=HELPERS + "\nfetch_all_pages(None, 'a', 'b')\n")
        v = bb_lints.lint_candidate(d)
        self.assertTrue(any(x.startswith("repl/helpers.py:") and "top level" in x for x in v), v)

    def test_direct_note_rules_apply_under_repl(self):
        d = self.repl_dir(notes={"nosuchapp": "x"})
        self.assertTrue(any(x.startswith("repl/api_notes.json:") for x in bb_lints.lint_candidate(d)))

    def test_other_files_in_repl_are_rejected(self):
        d = self.repl_dir()
        (d / "repl" / "extra.md").write_text("would be injected")
        self.assertTrue(any("repl/extra.md" in x for x in bb_lints.lint_candidate(d)))


class FakeDocs:
    """Stands in for apis.api_docs: the two lookups the notes wrap."""

    @staticmethod
    def show_api_doc(app_name=None, api_name=None):
        return {"app_name": app_name, "api_name": api_name}

    @staticmethod
    def show_api_descriptions(app_name=None):
        return [{"name": "show_song"}]


class TestReplCode(unittest.TestCase):
    """The REPL code init_repl loads: direct's setup code, safe to run again."""

    def load_n_times(self, notes, times=5):
        """Load the code ``times`` times: a weak model may call init_repl on every step."""
        import types
        code = tailoring.repl_code(HELPERS, notes)
        ns = {"apis": types.SimpleNamespace(api_docs=FakeDocs())}
        for _ in range(times):
            exec(code, ns)  # noqa: S102
        return ns

    def test_notes_wrap_the_lookups_once_however_often_it_runs(self):
        ns = self.load_n_times({"spotify.show_song": "N1", "spotify": "APP"})
        ns["apis"].api_docs.show_api_doc(app_name="spotify", api_name="show_song")  # a lookup in between
        exec(tailoring.repl_code(HELPERS, {"spotify.show_song": "N1", "spotify": "APP"}), ns)  # noqa: S102
        doc = ns["apis"].api_docs.show_api_doc(app_name="spotify", api_name="show_song")
        self.assertEqual(doc, {"app_name": "spotify", "api_name": "show_song", "note": "N1"})
        listed = ns["apis"].api_docs.show_api_descriptions(app_name="spotify")
        self.assertEqual(listed, [{"name": "show_song", "note": "N1"}, {"note": "APP"}])
        self.assertIs(ns["_cap_orig_show_api_doc"], FakeDocs.show_api_doc)

    def test_helpers_only_is_the_direct_code_plus_the_load_counter(self):
        import aw_agent
        code = tailoring.repl_code(HELPERS, {})
        self.assertTrue(code.startswith(aw_agent.setup_snippet(HELPERS, {})))
        self.assertIn(f'print("{tailoring.LOADS_MARKER}", _cap_repl_loads)', code)
        self.assertTrue(callable(self.load_n_times({})["fetch_all_pages"]))

    def test_the_load_counter_counts_every_load(self):
        import contextlib
        import io
        import types
        code = tailoring.repl_code(HELPERS, {"spotify": "n"})
        ns = {"apis": types.SimpleNamespace(api_docs=FakeDocs())}
        printed = []
        for _ in range(3):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, ns)  # noqa: S102
            printed.append(buf.getvalue().strip())
        self.assertEqual(printed, [f"{tailoring.LOADS_MARKER} 1", f"{tailoring.LOADS_MARKER} 2",
                                   f"{tailoring.LOADS_MARKER} 3"])

    def test_nothing_to_load(self):
        self.assertEqual(tailoring.repl_code('"""doc only"""\n', {}), "")


class TestInitReplTool(ReplMixin, unittest.TestCase):
    """The generated store tool: one public function, and what it sends to the trial's REPL."""

    def tool_ns(self, d: Path, context) -> dict:
        src = tailoring.build_init_repl(d)
        ns = {} if context is None else {"skillberry_context": context}
        exec(compile(src, "init_repl.py", "exec"), ns)  # noqa: S102
        return ns

    def test_one_public_tool_named_init_repl(self):
        import ast
        src = tailoring.build_init_repl(self.repl_dir(notes={"spotify": "n"}))
        public = [n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        self.assertEqual(public, ["init_repl"])
        self.assertNotIn("__AW_REPL_", src)

    def call_against_a_fake_repl(self, d: Path, times: int):
        """Call init_repl ``times`` times against a fake environment server whose /execute runs the
        code in ONE persistent namespace (like the trial's REPL) and returns what it printed."""
        import contextlib
        import http.server
        import io
        import threading
        import types
        seen, world = [], {"apis": types.SimpleNamespace(api_docs=FakeDocs())}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, req))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    exec(req["code"], world)  # noqa: S102
                body = json.dumps({"output": buf.getvalue()}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ns = self.tool_ns(d, {"env_id": "e", "task_id": "t1",
                                  "appworld": {"repl_port": str(srv.server_address[1])}})
            answers = [ns["init_repl"]() for _ in range(times)]
        finally:
            srv.shutdown()
            srv.server_close()
        return answers, seen, world

    def test_posts_the_code_to_the_trials_repl(self):
        d = self.repl_dir(notes={"spotify": "n"})
        (out,), seen, world = self.call_against_a_fake_repl(d, 1)
        path, req = seen[0]
        self.assertEqual(path, "/execute")
        self.assertEqual(req["task_id"], "t1")
        self.assertEqual(req["code"], tailoring.repl_code(*tailoring.read_repl(d)))
        self.assertTrue(out.startswith("REPL ready: "), out)
        self.assertIn("fetch_all_pages", out)
        self.assertIn("note", out)
        self.assertTrue(callable(world["fetch_all_pages"]))

    def test_every_answer_tells_the_model_to_stop_calling_it(self):
        # A weak model looped on init_repl inside one request (fix10, 2026-11-23): each answer must end
        # the tool step, and a repeat must say that nothing changed.
        d = self.repl_dir(notes={"spotify": "n"})
        answers, seen, world = self.call_against_a_fake_repl(d, 4)
        nxt = "Do not call init_repl again in this task. Reply now with your one ```python code block."
        self.assertTrue(answers[0].startswith("REPL ready: "), answers[0])
        for a in answers[1:]:
            self.assertTrue(a.startswith("Already loaded, nothing changed: "), a)
        for a in answers:
            self.assertTrue(a.endswith(nxt), a)
        self.assertEqual(len(seen), 4)
        doc = world["apis"].api_docs.show_api_doc(app_name="spotify", api_name="x")
        self.assertNotIn("note", doc)  # app note only (descriptions); the doc lookup is wrapped once
        self.assertIs(world["_cap_orig_show_api_doc"], FakeDocs.show_api_doc)

    def test_without_a_repl_in_the_context_it_says_so(self):
        d = self.repl_dir()
        for ctx in (None, {}, {"task_id": "t1"}, {"task_id": "t1", "appworld": {"repl_port": "9101/evaluate"}}):
            self.assertIn("no REPL", self.tool_ns(d, ctx)["init_repl"](), ctx)

    def test_deploy_stages_the_tool_only_when_repl_loads_something(self):
        staged = []

        class FakeBB:
            Protection = staticmethod(lambda: "protect")
            start_store = staticmethod(lambda: None)
            restart_spa = staticmethod(lambda name, **env: None)

            @staticmethod
            def reset_store_to_skill(d, name, protect):
                staged.append((sorted(str(p.relative_to(d)) for p in Path(d).rglob("*") if p.is_file()),
                               (Path(d) / "SKILL.md").read_text()))

        with patch.object(tailoring, "blackbox_env", lambda: FakeBB), \
                patch.object(tailoring, "spa_upstream_env", lambda: {"SPA_MODEL_NAME": "m"}):
            tailoring._deployed["digest"] = None
            seed = self.seed_dir()
            make_seed_skill.write_repl(seed)
            tailoring.deploy(seed)
            tailoring.deploy(self.repl_dir())
        self.assertEqual([files for files, _ in staged], [["SKILL.md"], ["SKILL.md", "scripts/init_repl.py"]])
        self.assertEqual(staged[0][1], (seed / "SKILL.md").read_text())        # the seed: unchanged
        self.assertIn("Pre-defined helper functions are available in the REPL", staged[1][1])
        self.assertIn(tailoring.INIT_REPL_LINE, staged[1][1])

    def test_digest_covers_the_repl_files(self):
        d = self.repl_dir()
        before = tailoring.skill_digest(d)
        (d / "repl" / "api_notes.json").write_text(json.dumps({"spotify": "changed"}))
        self.assertNotEqual(tailoring.skill_digest(d), before)

    def test_port_header(self):
        h = tailoring.context_headers("e", "t", SUPERVISOR, repl_port=9101)
        self.assertEqual(h["skillberry-context-appworld-repl_port"], "9101")
        self.assertNotIn("skillberry-context-appworld-repl_port", tailoring.context_headers("e", "t", SUPERVISOR))

    def test_repl_candidate_without_remote_repl_is_infra_and_not_deployed(self):
        bb_adapter = load_bb_adapter()
        d = self.repl_dir()
        task = bb_adapter.Task(id="t1", input={})
        with patch.object(bb_adapter.Adapter, "_check_seal", lambda self, ids: None), \
                patch.object(bb_adapter, "_wait_readable", lambda p, timeout_s=60.0: None), \
                patch.dict(os.environ, {"AW_REMOTE_REPL": "0"}), \
                patch.object(bb_adapter.tailoring, "deploy", side_effect=AssertionError("deployed")):
            out = bb_adapter.Adapter().run_trials([task], d, n_trials=1, base_seed=0)
        self.assertIn("needs the remote REPL", out["t1"][0].error)


class TestEpisodeSendsThePort(unittest.TestCase):
    def test_the_trials_port_is_in_the_headers(self):
        bb = load_bb_adapter()
        seen = {}

        def fake_run(argv, **kw):
            seen.update(json.loads(Path(argv[2]).read_text()))
            Path(argv[3]).write_text(json.dumps({"task_id": "a", "termination": "completed", "steps": [],
                                                 "disconnected": True}))
            return type("P", (), {"returncode": 0, "stderr": ""})()

        run = Path(tempfile.mkdtemp())
        with patch.object(bb, "_supervisor", lambda tid, root: SUPERVISOR), \
                patch.object(bb, "_client_prompt", lambda: Path("instructions.txt")), \
                patch.object(bb.subprocess, "run", fake_run), \
                patch.dict(os.environ, {"AW_INFRA_RETRIES": "0", "AW_REMOTE_REPL": "1"}), \
                patch.object(bb.tailoring, "start_env_server", lambda root, env: 9123), \
                patch.object(bb.tailoring, "stop_env_server", lambda port: True), \
                patch.object(bb.tailoring, "disconnect", lambda *a, **k: True):
            bb.Adapter()._episode(bb.Task(id="a", input={}), 0, run, run)
        self.assertEqual(seen["headers"]["skillberry-context-appworld-repl_port"], "9123")
        self.assertEqual(seen["remote_environment_url"], "http://127.0.0.1:9123")


class TestBlackboxReferenceNotes(unittest.TestCase):
    def test_built_from_the_direct_notes_with_the_blackbox_delivery(self):
        sys.path.insert(0, str(DIRECT / "scripts"))
        import make_reference
        notes = make_reference.blackbox_notes()
        self.assertNotIn("tools/helpers.py", notes)
        self.assertIn("repl/helpers.py", notes)
        self.assertIn("init_repl", notes)
        self.assertIn("bb_lints.py", notes)
        shared = "The episode ends when the code calls `apis.supervisor.complete_task(...)`"
        self.assertIn(shared, notes)
        self.assertIn(shared, make_reference.NOTES)


class TestCommittedSeed(unittest.TestCase):
    """seed_capability/my_skill is committed (like tau2's), with the app list as a placeholder."""

    def test_files(self):
        files = sorted(p.relative_to(SEED_SKILL).as_posix() for p in SEED_SKILL.rglob("*") if p.is_file()
                       and "__pycache__" not in p.parts)
        self.assertEqual(files, ["SKILL.md", "repl/api_notes.json", "repl/helpers.py", "scripts/init_repl.py"])

    def test_skill_md_is_built_from_the_stock_prompt_with_the_app_list_left_out(self):
        text = (SEED_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(text, make_seed_skill.build(STOCK))  # drift check: rebuild with --build
        self.assertEqual(text.count("{{ app_descriptions }}"), 1)
        self.assertEqual(text.count("{{ main_user."), 6)

    def test_render_fills_only_the_app_list(self):
        text = (SEED_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(make_seed_skill.render(text, APPS), make_seed_skill.build(STOCK, APPS))

    def test_repl_is_empty_so_nothing_is_deployed(self):
        self.assertEqual(tailoring.read_repl(SEED_SKILL), ('"""Helper functions loaded into the AppWorld REPL by '
                                                           'the init_repl store tool (capability: tools).\n\n'
                                                           'Empty in the seed. See optimizer/INSTRUCTIONS.md for the '
                                                           'rules a helper must follow.\n"""\n', {}))
        self.assertEqual(tailoring.build_init_repl(SEED_SKILL), "")

    def test_main_renders_the_committed_seed_into_a_project(self):
        out = Path(tempfile.mkdtemp()) / "seed_capability" / "my_skill"
        apps_json = out.parent / "apps.json"
        with patch.object(make_seed_skill, "render_app_descriptions", lambda: APPS):
            make_seed_skill.main(["x", str(SEED_SKILL), str(out), str(apps_json)])
        self.assertEqual((out / "SKILL.md").read_text(), make_seed_skill.build(STOCK, APPS))
        self.assertEqual((out / "scripts" / "init_repl.py").read_bytes(),
                         (SEED_SKILL / "scripts" / "init_repl.py").read_bytes())
        self.assertEqual(json.loads(apps_json.read_text()), {"text": APPS})


class TestFrozenInitRepl(LintDataMixin, unittest.TestCase):
    def test_specs_protect_it(self):
        for name in ("capevolve.yaml", "capevolve.smoke.yaml", "capevolve.fix10.yaml"):
            text = (EX / name).read_text()
            self.assertIn('protected_paths: [default, "my_skill/scripts/init_repl.py"]', text, name)
            self.assertIn("capability_path: seed_capability ", text, name)

    def test_core_flags_an_edit_as_tampering(self):
        # The harness's own seal: snapshot the candidate before the optimizer runs, verify after.
        from cap_evolve import integrity
        from cap_evolve.harness import parse_protected_paths
        cand = Path(tempfile.mkdtemp())
        (cand / "my_skill").mkdir()
        self.tmp = cand  # seed_dir() writes cand/my_skill
        self.seed_dir()
        pats = parse_protected_paths(["default", "my_skill/scripts/init_repl.py"])
        manifest = integrity.snapshot(cand, pats)
        self.assertTrue(integrity.verify(manifest, cand).ok)
        (cand / "my_skill" / "SKILL.md").write_text("an allowed edit")  # not protected
        self.assertTrue(integrity.verify(manifest, cand).ok)
        f = cand / "my_skill" / "scripts" / "init_repl.py"
        f.write_text(f.read_text() + "\n# edited\n")
        self.assertFalse(integrity.verify(manifest, cand).ok)

    def test_a_changed_copy_is_rejected(self):
        d = self.seed_dir()
        f = d / "scripts" / "init_repl.py"
        f.write_text(f.read_text().replace("/execute", "/evaluate"))
        self.assertTrue(any("init_repl.py: changed" in x for x in bb_lints.lint_candidate(d)))

    def test_a_deleted_copy_is_rejected(self):
        d = self.seed_dir()
        (d / "scripts" / "init_repl.py").unlink()
        self.assertTrue(any("init_repl.py: missing" in x for x in bb_lints.lint_candidate(d)))

    def test_deploy_uses_the_benchmarks_copy_not_the_candidates(self):
        d = self.seed_dir()
        (d / "repl").mkdir()
        (d / "repl" / "helpers.py").write_text(HELPERS)
        (d / "scripts" / "init_repl.py").write_text("def init_repl():\n    return 'tampered'\n")
        self.assertIn("urllib.request", tailoring.build_init_repl(d))
        self.assertNotIn("tampered", tailoring.build_init_repl(d))


class TestTau2StyleLayout(LintDataMixin, unittest.TestCase):
    """capability_path: seed_capability, so a candidate is the folder that HOLDS my_skill/."""

    def test_lint_and_skill_dir_resolve_my_skill(self):
        d = self.seed_dir()  # self.tmp / my_skill
        self.assertEqual(bb_lints.skill_dir(self.tmp), d)
        self.assertEqual(bb_lints.skill_dir(d), d)
        (self.tmp / "INSTRUCTIONS.md").write_text("the harness's own file, next to my_skill/")
        self.assertEqual(bb_lints.lint_candidate(self.tmp), [])

    def test_run_trials_deploys_my_skill_and_writes_native_sims_for_the_candidate(self):
        bb_adapter = load_bb_adapter()
        run = Path(tempfile.mkdtemp())
        (run / "splits.json").write_text(json.dumps({"train": ["t1"], "val": ["t1"], "test": []}))
        cand = run / "work" / "cand_0001"
        (cand / "my_skill").mkdir(parents=True)
        for p in self.seed_dir().rglob("*"):
            if p.is_file():
                dest = cand / "my_skill" / p.relative_to(self.tmp / "my_skill")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(p.read_bytes())
        deployed, natives = [], []
        task = bb_adapter.Task(id="t1", input={})
        with patch.object(bb_adapter.Adapter, "_check_seal", lambda self, ids: None), \
                patch.object(bb_adapter, "_wait_readable", lambda p, timeout_s=60.0: None), \
                patch.object(bb_adapter.tailoring, "deploy", lambda d: deployed.append(Path(d))), \
                patch.object(bb_adapter.Adapter, "_episode",
                             lambda self, t, s, r, w, native_path=None: natives.append(native_path)
                             or bb_adapter.Rollout(task_id=t.id)):
            bb_adapter.Adapter().run_trials([task], cand, n_trials=1, base_seed=0)
        self.assertEqual(deployed, [cand.resolve() / "my_skill"])
        self.assertEqual(natives[0].parent, run.resolve() / "native_sims" / "cand_0001" / "val")


if __name__ == "__main__":
    unittest.main()
