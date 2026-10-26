from datetime import datetime, timezone

import pytest

from codelore import gitdata, heatmap, postmortem, why
from codelore.llm import LLMError, parse_json_reply, strip_unknown_citations

from conftest import FakeModel

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


# --- git data -----------------------------------------------------------------------------------

def test_extract_pr_numbers():
    assert gitdata.extract_pr_numbers("Fix thing (#42)") == [42]
    assert gitdata.extract_pr_numbers("Merge pull request #7 from x/y") == [7]
    assert gitdata.extract_pr_numbers("no refs here") == []


def test_line_history_follows_the_line(repo):
    commits = gitdata.line_history(repo, "billing.py", 2)
    assert [c.subject for c in commits] == ["Fix double charge when payment webhook retries (#42)"]
    assert commits[0].pr_numbers == [42]
    assert "retries webhooks" in commits[0].body


def test_remote_slug(repo):
    assert gitdata.remote_slug(repo) == "acme/shop"


# --- why ----------------------------------------------------------------------------------------

def test_why_without_model_returns_raw_history(repo):
    result = why.explain(str(repo), "billing.py", 2, model=None, fetch_prs=False)
    assert result["story"] is None
    assert "not configured" in result["error"]
    assert result["evidence"][0]["id"] == "C1"
    assert result["evidence"][0]["url"].startswith("https://github.com/acme/shop/commit/")


def test_why_strips_citations_to_unknown_evidence(repo):
    model = FakeModel({"summary": "Guards against double charges [C1].",
                       "story": "Webhook retries charged twice [C1][P9]."})
    result = why.explain(str(repo), "billing.py", 2, model=model, fetch_prs=False)
    assert result["story"] == "Webhook retries charged twice [C1]."
    assert result["removed_citations"] == ["P9"]
    assert ">>     2 |     if order.charged" in model.calls[0][1]


def test_why_rejects_out_of_range_line(repo):
    with pytest.raises(ValueError):
        why.explain(str(repo), "billing.py", 999, model=None, fetch_prs=False)


# --- heat map -----------------------------------------------------------------------------------

def test_heatmap_flags_sole_owner_and_ignores_lockfiles(repo):
    files = {f.path: f for f in heatmap.analyze(str(repo), since="2 years ago", now=NOW)}
    assert "package-lock.json" not in files
    billing, utils = files["billing.py"], files["utils.py"]
    assert billing.bus_factor == 1 and billing.top_author == "Rahul" and billing.top_share == 1.0
    assert "only Rahul knows this" in billing.flags
    assert "Rahul inactive 236d" in billing.flags
    assert billing.fix_commits == 4
    assert utils.authors == 2 and not any("knows this" in f for f in utils.flags)
    assert billing.risk > utils.risk


def test_heatmap_notes_attach_to_files(repo):
    files = heatmap.analyze(str(repo), since="2 years ago", now=NOW)
    model = FakeModel({"notes": {"billing.py": "Four fixes in two months, all by Rahul."}})
    assert heatmap.add_notes(files, model) is None
    assert files[0].note == "Four fixes in two months, all by Rahul."


def test_percentiles_handle_ties():
    assert heatmap._percentiles([1, 1, 5]) == [0.0, 0.0, 1.0]


# --- postmortem ---------------------------------------------------------------------------------

THREAD = """[14:02] maya: checkout is throwing 500s
[14:05] dev: looks like the db pool is exhausted
continuing: max_connections hit
[14:20] maya: rolled back deploy 812, errors dropping
"""
LOGS = "\n".join(
    ["2026-10-20T14:01:00Z INFO started"]
    + [f"2026-10-20T14:0{i}:00Z ERROR pool exhausted after {i}000ms" for i in range(2, 6)]
)


def test_build_inputs_numbers_and_collapses():
    inp = postmortem.build_inputs(THREAD, LOGS, "We saw latency. Then errors.")
    ids = [e.id for e in inp.evidence]
    assert ids[:3] == ["T1", "T2", "T3"]
    assert "max_connections" in inp.evidence[1].text  # continuation line joined
    assert inp.participants == ["maya", "dev"]
    logs = [e for e in inp.evidence if e.source == "logs"]
    assert any("repeated x4" in e.text for e in logs)
    assert [e.id for e in inp.evidence if e.source == "voice"] == ["V1", "V2"]


def test_slack_export_parsing():
    raw = '[{"ts": "1792504920.0", "user_name": "maya", "text": "site down"}, {"ts": "1", "subtype": "join"}]'
    msgs = postmortem.parse_thread(raw)
    assert msgs == [("2026-10-20 14:02:00Z", "maya", "site down")]


DRAFT = {
    "title": "Checkout 500s",
    "summary": "Checkout failed for 18 minutes.",
    "impact": "Customers could not pay.",
    "severity": "SEV2",
    "detection": {"text": "Reported in the incident channel.", "sources": ["T1"]},
    "timeline": [
        {"time": "14:02", "event": "500s reported", "sources": ["T1"]},
        {"time": "14:10", "event": "Invented event", "sources": ["T99"]},
    ],
    "root_cause": {"text": "Deploy 812 exhausted the DB pool; maya failed to check the pool size.", "sources": ["T2", "L2"]},
    "contributing_factors": [],
    "what_went_well": [{"text": "Fast rollback", "sources": ["T3"]}],
    "action_items": [{"action": "Alert on pool saturation", "owner": "DB team", "priority": "P1", "sources": ["L2"]}],
}


def test_postmortem_moves_unsupported_items_and_rewrites_blame():
    rewrite = {"rewrites": {"root_cause.text": "Deploy 812 exhausted the DB pool; no check guarded pool size."}}
    model = FakeModel(DRAFT, rewrite)
    result = postmortem.write(postmortem.build_inputs(THREAD, LOGS), model)
    pm = result["postmortem"]
    assert [t["event"] for t in pm["timeline"]] == ["500s reported"]
    assert result["needs_review"][0]["event"] == "Invented event"
    assert pm["root_cause"]["text"].endswith("no check guarded pool size.")
    assert result["blame_findings"] == []
    assert result["blame_rewrites"] == 1
    md = result["markdown"]
    assert "## Needs review" in md and "**L2**" in md and "T99" not in md


def test_postmortem_keeps_warning_when_rewrite_fails():
    model = FakeModel(DRAFT, "not json")
    result = postmortem.write(postmortem.build_inputs(THREAD, LOGS), model)
    assert result["blame_findings"][0]["phrases"] == ["failed to"]
    assert "## Blame-language warnings" in result["markdown"]


def test_postmortem_requires_evidence():
    with pytest.raises(ValueError):
        postmortem.write(postmortem.build_inputs(""), FakeModel())


# --- llm helpers --------------------------------------------------------------------------------

def test_parse_json_reply_variants():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json_reply("nothing")


def test_strip_unknown_citations():
    assert strip_unknown_citations("x [C1] y [C7]", {"C1"}) == ("x [C1] y ", ["C7"])


def test_file_kind_buckets():
    k = heatmap.file_kind
    assert [k("src/app.py"), k("tests/test_app.py"), k(".github/workflows/ci.yml"), k("docs/index.md")] == [
        "code", "test", "config", "docs"]


def test_heatmap_kind_filter(repo):
    from conftest import commit
    commit(repo, "tests/test_billing.py", "def test_x(): pass\n", "fix flaky test", date="2026-03-05T10:00:00Z")
    code_only = {f.path for f in heatmap.analyze(str(repo), since="2 years ago", now=NOW)}
    with_tests = {f.path for f in heatmap.analyze(str(repo), since="2 years ago", kinds=("code", "test"), now=NOW)}
    assert "tests/test_billing.py" not in code_only and "tests/test_billing.py" in with_tests
