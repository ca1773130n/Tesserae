"""Smart scope router for `ask` (replaces the active-project default)."""

from __future__ import annotations

from tesserae.ask_router import Route, is_followup, route_ask

NAMES = ["alpha", "beta", "gamma"]


def test_comparative_question_federates_over_all():
    r = route_ask("what is the best project in terms of creativity?", NAMES)
    assert r.scope == "federated" and set(r.aliases) == set(NAMES)


def test_names_a_single_project_routes_there():
    r = route_ask("how does alpha compile work?", NAMES)
    assert r.scope == "current" and r.aliases == ["alpha"]


def test_names_multiple_projects_federates_those():
    r = route_ask("relate alpha to beta", NAMES)
    assert r.scope == "federated" and r.aliases == ["alpha", "beta"]


def test_ambiguous_question_falls_back_to_federated_all():
    r = route_ask("what did I decide about caching?", NAMES)
    assert r.scope == "federated" and r.aliases == NAMES
    assert "default" in r.reason


def test_followup_keeps_previous_route():
    history = [Route("current", ["alpha"], "named alpha")]
    r = route_ask("and why?", NAMES, history=history)
    assert r.scope == "current" and r.aliases == ["alpha"]


def test_topic_shift_to_other_project_reroutes():
    history = [Route("current", ["alpha"], "named alpha")]
    r = route_ask("what about beta?", NAMES, history=history)
    assert r.scope == "current" and r.aliases == ["beta"]


def test_short_question_inside_a_project_routes_there():
    r = route_ask("more", NAMES, cwd_alias="gamma")
    assert r.scope == "current" and r.aliases == ["gamma"]


def test_single_and_zero_project_registries():
    assert route_ask("anything", ["solo"]).aliases == ["solo"]
    assert route_ask("anything", []).aliases == []


def test_llm_classifier_used_only_for_the_ambiguous_middle():
    calls = []

    def fake_llm(question, names, history):
        calls.append(question)
        return Route("current", ["beta"], "llm chose beta")

    # comparative -> handled by heuristic, llm NOT consulted
    route_ask("compare alpha and beta", NAMES, llm_classify=fake_llm)
    assert calls == []
    # genuinely ambiguous -> llm consulted
    r = route_ask("tell me the gist of recent work", NAMES, llm_classify=fake_llm)
    assert calls and r.aliases == ["beta"]


def test_is_followup():
    assert is_followup("and why?")
    assert is_followup("more")
    assert not is_followup("how does the federated retrieval pipeline assemble graphs?")
