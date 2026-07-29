"""Project adapter — optimize tau2-bench AIRLINE via SPA + Skillberry Store.

Wires cap-evolve to the tau2 airline domain with LLM calls routed through
Skillberry Proxy-Agent (SPA). Primitive tools (14 functions) are frozen in the
store. The optimizer creates NEW composite skills whose SKILL.md snippets SPA
translates into system prompt enrichment.

  * ``tasks``      -> all 50 airline tasks (stable, non-empty for every split).
  * ``run_batch``  -> tau2's own batch runner (``run_tasks``) with LLM calls
                      routed through SPA (agent) or direct upstream (user sim).
  * ``run_target`` -> thin wrapper over ``run_batch`` for one task.
  * ``score``      -> tau2's own reward in [0,1] (deterministic given a rollout);
                      gold-AWARE but gold-SAFE, ARGUMENT-LEVEL feedback.
  * ``apply``      -> uploads the candidate's composite skill to the store,
                      restarts SPA with SKILL_NAME=<candidate>, waits for health.

``cap-evolve check`` does NO live LLM call: ``tasks``/``score``/``materialize``
are network-free, and SPA endpoint resolution is lazy.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cap_evolve import CapabilityAdapter, Rollout, Score, Task

import spa_env

DOMAIN = "airline_skillberry"

TAU2_LOG_DIR = Path(os.environ.get("TAU2_LOG_DIR", "/tmp"))


@contextlib.contextmanager
def _tee_to_log(label: str = "run"):
    """Capture stdout and write it to both stderr (visible in terminal) and a log file."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = TAU2_LOG_DIR / f"tau2_{DOMAIN}_{label}_{timestamp}.log"
    buf = io.StringIO()

    class Tee:
        def write(self, data):
            buf.write(data)
            sys.__stderr__.write(data)

        def flush(self):
            buf.flush()
            sys.__stderr__.flush()

    old_stdout = sys.stdout
    sys.stdout = Tee()
    try:
        yield log_path
    finally:
        sys.stdout = old_stdout
        log_path.write_text(buf.getvalue(), encoding="utf-8")
        print(f"  tau2 log: {log_path}", file=sys.__stderr__)


def _shown_metrics(reward: float, reward_info: dict, rollout) -> list:
    """Shown-only secondary metrics for display; the GATE still uses reward (primary)."""
    metrics = [{"name": "reward", "value": float(reward), "primary": True, "direction": "higher"}]
    db_check = (reward_info or {}).get("db_check") or {}
    if "db_match" in db_check:
        metrics.append({"name": "db_match", "value": 1.0 if db_check.get("db_match") else 0.0,
                        "primary": False, "direction": "higher"})
    metrics.append({"name": "cost_usd", "value": float(getattr(rollout, "cost_usd", 0.0) or 0.0),
                    "primary": False, "direction": "lower"})
    return metrics


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class Adapter(CapabilityAdapter):

    _current_skill_name: str | None = None

    # ---- tasks -----------------------------------------------------------

    def tasks(self, split: str) -> list[Task]:
        """Return ALL 50 tau2 airline tasks for any split (stable, non-empty)."""
        from tau2.domains.airline.environment import get_tasks as airline_get_tasks

        tau2_tasks = airline_get_tasks()
        out: list[Task] = []
        for t in tau2_tasks:
            out.append(
                Task(
                    id=str(t.id),
                    input=str(getattr(t, "id", "")),
                    metadata={"domain": DOMAIN},
                )
            )
        return out

    # ---- running ---------------------------------------------------------

    def _tau2_tasks_by_id(self):
        from tau2.domains.airline.environment import get_tasks as airline_get_tasks
        return {str(t.id): t for t in airline_get_tasks()}

    def run_batch(self, tasks: list[Task], ctx, *, seed: int = 0) -> dict:
        """Run a batch of airline tasks through tau2's own batch runner.

        LLM calls for the agent are routed through SPA (via ibm/skillberry-local
        model string). User simulator calls go directly to the upstream LLM.
        """
        from tau2.run import run_tasks

        by_id = self._tau2_tasks_by_id()
        tau2_tasks = [by_id[t.id] for t in tasks if t.id in by_id]
        results: dict[str, Rollout] = {}

        for t in tasks:
            if t.id not in by_id:
                results[t.id] = Rollout(
                    task_id=t.id, error=f"task id {t.id} not found in airline task set"
                )
        if not tau2_tasks:
            return results

        agent_m = spa_env.agent_model()
        user_m = spa_env.user_model()
        max_concurrency = int(os.environ.get("TAU2_MAX_CONCURRENCY", "100"))

        with _tee_to_log("batch"):
            sim_results = run_tasks(
                domain=DOMAIN,
                tasks=tau2_tasks,
                agent="llm_agent",
                user="user_simulator",
                llm_agent=agent_m,
                llm_args_agent=spa_env.llm_args_for(agent_m),
                llm_user=user_m,
                llm_args_user=spa_env.llm_args_for(user_m),
                num_trials=1,
                max_steps=100,
                max_errors=10,
                max_concurrency=max_concurrency,
                seed=int(seed),
                save_to=None,
                console_display=False,
            )

        for sim in sim_results.simulations:
            rollout = self._sim_to_rollout(sim)
            results[str(rollout.task_id)] = rollout

        for t in tasks:
            if t.id not in results:
                results[t.id] = Rollout(
                    task_id=t.id,
                    error="no simulation produced for task (tau2 returned nothing)",
                    metadata={"domain": DOMAIN, "tau2_reward": 0.0},
                )
        return results

    @staticmethod
    def _sim_to_rollout(sim) -> Rollout:
        """Map one tau2 SimulationRun to a cap-evolve Rollout."""
        from tau2.data_model.simulation import TerminationReason

        infra_reasons = {
            TerminationReason.TOO_MANY_ERRORS,
            TerminationReason.TASK_FAILED,
        }

        task_id = str(sim.task_id)
        reward_info = sim.reward_info
        reward = (
            float(reward_info.reward)
            if reward_info is not None and reward_info.reward is not None
            else 0.0
        )
        agent_cost = sim.agent_cost or 0.0
        user_cost = sim.user_cost or 0.0
        term = sim.termination_reason
        error = None
        if term in infra_reasons:
            error = f"tau2 terminated for infrastructure reason: {term}"

        try:
            messages = [m.model_dump() for m in sim.get_messages()]
        except Exception:
            messages = None

        reward_info_dump = (
            reward_info.model_dump(mode="json") if reward_info is not None else None
        )

        return Rollout(
            task_id=task_id,
            output=messages,
            trace=messages,
            cost_usd=float(agent_cost) + float(user_cost),
            tokens=0,
            error=error,
            metadata={
                "domain": DOMAIN,
                "tau2_reward": reward,
                "tau2_reward_info": reward_info_dump,
                "termination_reason": str(term),
            },
        )

    def run_trials(
        self, tasks: list[Task], ctx, *, n_trials: int, base_seed: int
    ) -> dict[str, list[Rollout]]:
        """Run ALL trials in ONE tau2 run_tasks call."""
        from tau2.run import run_tasks

        n_trials = int(n_trials)
        by_id = self._tau2_tasks_by_id()
        tau2_tasks = [by_id[t.id] for t in tasks if t.id in by_id]

        results: dict[str, list[Rollout]] = {t.id: [None] * n_trials for t in tasks}

        for t in tasks:
            if t.id not in by_id:
                results[t.id] = [
                    Rollout(task_id=t.id, error=f"task id {t.id} not found in airline task set")
                    for _ in range(n_trials)
                ]
        if not tau2_tasks or n_trials <= 0:
            return results

        agent_m = spa_env.agent_model()
        user_m = spa_env.user_model()
        max_concurrency = int(os.environ.get("TAU2_MAX_CONCURRENCY", "125"))

        with _tee_to_log("trials"):
            sim_results = run_tasks(
                domain=DOMAIN,
                tasks=tau2_tasks,
                agent="llm_agent",
                user="user_simulator",
                llm_agent=agent_m,
                llm_args_agent=spa_env.llm_args_for(agent_m),
                llm_user=user_m,
                llm_args_user=spa_env.llm_args_for(user_m),
                num_trials=n_trials,
                max_steps=100,
                max_errors=10,
                max_concurrency=max_concurrency,
                seed=int(base_seed),
                save_to=None,
                console_display=False,
            )

        for sim in sim_results.simulations:
            task_id = str(sim.task_id)
            trial = int(getattr(sim, "trial", 0) or 0)
            slot = results.get(task_id)
            if slot is None:
                slot = results[task_id] = [None] * n_trials
            if 0 <= trial < n_trials:
                slot[trial] = self._sim_to_rollout(sim)

        return results

    def run_target(self, task: Task, ctx, *, seed: int = 0) -> Rollout:
        """Run a single task by delegating to run_batch."""
        batch = self.run_batch([task], ctx, seed=seed)
        return batch.get(task.id, Rollout(task_id=task.id, error="no rollout produced"))

    # ---- scoring ---------------------------------------------------------

    def score(self, task: Task, rollout: Rollout) -> Score:
        """Score a rollout with tau2's own reward; gold-AWARE, gold-SAFE feedback."""
        meta = rollout.metadata or {}

        if rollout.error:
            return Score(
                task_id=task.id,
                reward=0.0,
                feedback=(
                    "Rollout did not complete for an infrastructure reason "
                    f"({rollout.error}). This is uncontrollable noise, not an agent "
                    "policy/tool failure; do not optimize against it."
                ),
                metrics=_shown_metrics(0.0, {}, rollout),
            )

        reward = float(meta.get("tau2_reward", 0.0) or 0.0)
        reward_info = meta.get("tau2_reward_info") or {}

        ctx = dict(meta)
        ctx["trace"] = rollout.trace or rollout.output or meta.get("trace") or []

        feedback = self._build_feedback(reward, reward_info, ctx)
        return Score(
            task_id=task.id, reward=reward, feedback=feedback,
            metrics=_shown_metrics(reward, reward_info, rollout),
        )

    # ---- making a candidate live ----------------------------------------

    def apply(self, candidate_dir, edits=None) -> None:
        """Deploy the candidate's composite skill: upload to store, restart SPA.

        1. Write edits to candidate_dir (pure) if any.
        2. Find the new composite skill sub-package(s) in candidate_dir.
        3. Upload each to the store via POST /skills/import-anthropic.
        4. Restart SPA with SKILL_NAME=<composite_skill_name>.
        """
        if edits:
            self.materialize(candidate_dir, edits)

        candidate_dir = Path(candidate_dir)

        # Find composite skill dirs (those with SKILL.md, excluding the frozen primitive_skill)
        skill_dirs = [
            d for d in sorted(candidate_dir.iterdir())
            if d.is_dir() and (d / "SKILL.md").exists() and d.name != "primitive_skill"
        ]

        if not skill_dirs:
            return

        # Upload each composite skill to the store
        for skill_dir in skill_dirs:
            spa_env.upload_skill(skill_dir)

        # Restart SPA with the last (newest) composite skill
        skill_name = skill_dirs[-1].name
        if skill_name != Adapter._current_skill_name:
            spa_env.restart_spa(skill_name)
            Adapter._current_skill_name = skill_name

    # ---- gold-safe feedback builder --------------------------------------

    @staticmethod
    def _iter_agent_tool_calls(meta: dict):
        """Yield (tool_name, arguments) for every ASSISTANT tool call in the trace."""
        for msg in meta.get("trace") or []:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name")
                args = tc.get("arguments") or {}
                if name:
                    yield str(name), (args if isinstance(args, dict) else {})

    @staticmethod
    def _user_profile_facts(meta: dict) -> dict:
        """Derive what the AGENT observed about the user's own profile/state."""
        import json
        import re

        payment_ids: list[str] = []
        reservation_ids: list[str] = []
        seen_p: set[str] = set()
        seen_r: set[str] = set()

        for msg in meta.get("trace") or []:
            if not isinstance(msg, dict) or msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue
            obj = None
            try:
                obj = json.loads(content)
            except Exception:
                obj = None

            if isinstance(obj, dict):
                pm = obj.get("payment_methods")
                if isinstance(pm, dict):
                    for pid in pm.keys():
                        if pid not in seen_p:
                            seen_p.add(pid)
                            payment_ids.append(str(pid))
                elif isinstance(pm, list):
                    for entry in pm:
                        pid = entry.get("id") if isinstance(entry, dict) else None
                        if pid and pid not in seen_p:
                            seen_p.add(pid)
                            payment_ids.append(str(pid))
                res = obj.get("reservations")
                if isinstance(res, list):
                    for rid in res:
                        rid = str(rid)
                        if rid not in seen_r:
                            seen_r.add(rid)
                            reservation_ids.append(rid)
                rid = obj.get("reservation_id")
                if rid and str(rid) not in seen_r:
                    seen_r.add(str(rid))
                    reservation_ids.append(str(rid))
            else:
                for pid in re.findall(r"\b(?:credit_card|gift_card|certificate)_\d+\b", content):
                    if pid not in seen_p:
                        seen_p.add(pid)
                        payment_ids.append(pid)

        return {"payment_methods": payment_ids, "reservation_ids": reservation_ids}

    @classmethod
    def _localize_action(cls, gold_name: str, gold_keys: list[str], meta: dict, facts: dict) -> str:
        """Argument-level, gold-SAFE detail for one failed action check."""
        agent_calls = [args for (n, args) in cls._iter_agent_tool_calls(meta) if n == gold_name]
        if not agent_calls:
            return f"{gold_name}: was never called (or not called correctly)"

        keys = gold_keys or sorted({k for c in agent_calls for k in c.keys()})
        used = agent_calls[-1]
        parts: list[str] = []
        for k in keys:
            v = used.get(k, "<missing>")
            detail = f"{k}={v!r}"
            kl = k.lower()
            if "payment" in kl and facts.get("payment_methods"):
                avail = facts["payment_methods"]
                if v not in avail:
                    detail += f" (not on the user's profile; available={avail})"
            elif ("reservation" in kl or kl in {"reservation_id", "target", "res_id"}) and facts.get(
                "reservation_ids"
            ):
                avail = facts["reservation_ids"]
                if v not in avail:
                    detail += f" (not among the user's reservations; held={avail})"
            parts.append(detail)
        return f"{gold_name}: agent used " + ", ".join(parts)

    @classmethod
    def _localize_communicate(cls, check: dict, meta: dict, facts: dict) -> str | None:
        """Name a derivable un-stated value for a missed communicate check (gold-safe)."""
        info = str(check.get("info") or "").lower()
        if "total" in info and ("cost" in info or "price" in info or "$" in info):
            total = cls._derive_total_cost(meta)
            if total is not None:
                return f"did not state the computed total cost (derivable from your own observed amounts: ${total:.2f})"
            return "did not state the computed total cost (sum the amounts you already observed and state it)"
        return None

    @staticmethod
    def _derive_total_cost(meta: dict):
        """Best-effort sum of payment amounts the AGENT itself observed."""
        import json

        total = 0.0
        found = False
        for _name, args in Adapter._iter_agent_tool_calls(meta):
            pay = args.get("payment") if isinstance(args, dict) else None
            if isinstance(pay, dict) and isinstance(pay.get("amount"), (int, float)):
                total += float(pay["amount"])
                found = True
            elif isinstance(args.get("amount"), (int, float)):
                total += float(args["amount"])
                found = True
        if found:
            return total
        for msg in meta.get("trace") or []:
            if not isinstance(msg, dict) or msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            try:
                obj = json.loads(content)
            except Exception:
                continue
            if isinstance(obj, dict):
                for k in ("total", "total_cost", "amount"):
                    if isinstance(obj.get(k), (int, float)):
                        total += float(obj[k])
                        found = True
        return total if found else None

    @classmethod
    def _build_feedback(cls, reward: float, reward_info: dict, meta: dict) -> str:
        """Argument-level, gold-SAFE learning signal."""
        if not reward_info:
            if reward >= 1.0:
                return "Task fully solved (reward 1.0)."
            return (
                f"Task scored {reward:.3f}. No detailed check breakdown is available "
                "for this rollout."
            )

        facts = cls._user_profile_facts(meta)
        lines: list[str] = [f"Task reward: {reward:.3f}."]

        db_check = reward_info.get("db_check")
        if db_check is not None and not db_check.get("db_match", True):
            lines.append(
                "Database state does NOT match the expected final state — a "
                "required write (book/update/cancel) was missing, wrong, or extra."
            )

        action_checks = reward_info.get("action_checks") or []
        details: list[str] = []
        for ac in action_checks:
            if ac.get("action_match", True):
                continue
            action = ac.get("action") or {}
            name = action.get("name") or action.get("func_name") or "an action"
            gold_keys = action.get("compare_args")
            if not gold_keys:
                gold_args = action.get("arguments")
                gold_keys = sorted(gold_args.keys()) if isinstance(gold_args, dict) else []
            try:
                details.append(cls._localize_action(str(name), list(gold_keys or []), meta, facts))
            except Exception:
                details.append(f"{name}: not performed correctly (right tool, right arguments)")
        if details:
            lines.append("Action-level defects (your own wrong values): " + "; ".join(details) + ".")

        communicate_checks = reward_info.get("communicate_checks") or []
        missed_comm = [c for c in communicate_checks if not c.get("met", True)]
        if missed_comm:
            comm_details: list[str] = []
            for c in missed_comm:
                try:
                    d = cls._localize_communicate(c, meta, facts)
                except Exception:
                    d = None
                if d:
                    comm_details.append(d)
            if comm_details:
                lines.append("Communication misses: " + "; ".join(comm_details) + ".")
            else:
                lines.append(
                    f"{len(missed_comm)} required piece(s) of information were not clearly "
                    "communicated to the user."
                )

        nl_assertions = reward_info.get("nl_assertions") or []
        missed_nl = [n for n in nl_assertions if not n.get("met", True)]
        if missed_nl:
            lines.append(
                f"{len(missed_nl)} behavioral expectation(s) were not met."
            )

        env_assertions = reward_info.get("env_assertions") or []
        missed_env = [e for e in env_assertions if not e.get("met", True)]
        if missed_env:
            lines.append(
                f"{len(missed_env)} environment assertion(s) failed."
            )

        if reward >= 1.0 and len(lines) == 1:
            lines.append("All checks passed.")

        return " ".join(lines)
