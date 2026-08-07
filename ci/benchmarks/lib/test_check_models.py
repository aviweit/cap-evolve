import json

import check_models  # same dir; run pytest from ci/benchmarks/lib

# The 34 ids the ete-litellm gateway served to the CI key on 2026-08-07, trimmed to the
# ones these tests reason about. Shapes and spellings are verbatim from the live response —
# note `azure/` vs `Azure/` coexisting, which is what makes case drift a real hazard.
SERVED = [
    "Azure/gpt-5-mini-2025-08-07",
    "aws/gpt-oss-120b",
    "aws/claude-haiku-4-5",
    "claude-opus-4-8",
    "azure/gpt-5.6-sol",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]
BODY = json.dumps({"data": [{"id": m, "object": "model"} for m in SERVED]})


def test_served_ids_accepts_data_envelope():
    assert check_models.served_ids(BODY) == SERVED


def test_served_ids_accepts_bare_list_and_plain_strings():
    # Different LiteLLM versions have shipped each shape; a preflight that crashes on one
    # would block runs it should wave through.
    assert check_models.served_ids(json.dumps(SERVED)) == SERVED
    assert check_models.served_ids(json.dumps([{"id": "a"}, "b", {}, {"id": None}])) == ["a", "b"]


def test_accepts_models_that_are_served():
    ok, lines = check_models.check(BODY, {"agent": "Azure/gpt-5-mini-2025-08-07",
                                          "optimizer": "claude-opus-4-8"})
    assert ok
    assert not [ln for ln in lines if "::error::" in ln]
    assert any("agent = Azure/gpt-5-mini-2025-08-07" in ln for ln in lines)


def test_rejects_unserved_model_and_lists_alternatives():
    # The exact failure of run 31124146014, from the other key's perspective.
    ok, lines = check_models.check(BODY, {"agent": "Azure/gpt-5-nano-2025-08-07"})
    assert not ok
    blob = "\n".join(lines)
    assert "is NOT served" in blob
    assert "aws/gpt-oss-120b" in blob  # full served list is printed for the fixer


def test_diagnoses_prefix_drift():
    # dropdown `gcp/gemini-2.5-flash` vs gateway `gemini-2.5-flash`
    ok, lines = check_models.check(BODY, {"agent": "gcp/gemini-2.5-flash"})
    assert not ok
    blob = "\n".join(lines)
    assert "prefix mismatch" in blob
    assert "the gateway spells it 'gemini-2.5-flash'" in blob


def test_diagnoses_case_drift():
    ok, lines = check_models.check(BODY, {"agent": "AWS/GPT-OSS-120B"})
    assert not ok
    blob = "\n".join(lines)
    assert "case mismatch" in blob
    assert "'aws/gpt-oss-120b'" in blob


def test_reports_every_bad_role_not_just_the_first():
    ok, lines = check_models.check(BODY, {"agent": "nope/one", "optimizer": "nope/two"})
    assert not ok
    blob = "\n".join(lines)
    assert "'nope/one'" in blob and "'nope/two'" in blob


def test_empty_model_list_is_a_failure_not_a_pass():
    # An empty list means we learned nothing; treating that as "entitled" would restore the
    # silent-0.000 hole this check exists to close.
    ok, lines = check_models.check(json.dumps({"data": []}), {"agent": "anything"})
    assert not ok
    assert any("no usable model ids" in ln for ln in lines)


def test_cli_passes_for_served_model(tmp_path):
    p = tmp_path / "models.json"
    p.write_text(BODY)
    assert check_models.main([str(p), "--require", "agent=claude-opus-4-8"]) == 0


def test_cli_fails_for_unserved_model(tmp_path):
    p = tmp_path / "models.json"
    p.write_text(BODY)
    assert check_models.main([str(p), "--require", "agent=Azure/gpt-4o"]) == 1


def test_cli_is_non_blocking_on_unparseable_body(tmp_path):
    # A 403 HTML error page is not evidence the model is unavailable — the completion probe
    # that follows is the judge. Blocking here would fail runs that would have worked.
    p = tmp_path / "models.json"
    p.write_text("<html>403 Forbidden</html>")
    assert check_models.main([str(p), "--require", "agent=claude-opus-4-8"]) == 0


def test_cli_rejects_malformed_require(tmp_path):
    p = tmp_path / "models.json"
    p.write_text(BODY)
    assert check_models.main([str(p), "--require", "agentclaude"]) == 2
