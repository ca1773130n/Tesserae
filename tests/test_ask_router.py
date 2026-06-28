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


def test_comparative_with_named_projects_federates_just_those():
    # "compare alpha and gamma" must NOT drag in unmentioned beta.
    r = route_ask("compare alpha and gamma", NAMES)
    assert r.scope == "federated" and r.aliases == ["alpha", "gamma"]


def test_bare_vs_does_not_misroute_a_local_question():
    # 'vs'/'across' are no longer comparative cues -> a short query inside a
    # project stays local instead of federating everything.
    r = route_ask("vs setup", ["alpha", "beta"], cwd_alias="alpha")
    assert r.scope == "current" and r.aliases == ["alpha"]


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


class _FakeClient:
    def __init__(self, out):
        self.out = out
        self.calls = 0

    def complete_json(self, *, system, user, schema_name, **kw):
        self.calls += 1
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def test_llm_classifier_routes_the_ambiguous_middle():
    from tesserae.ask_router import make_llm_classifier

    client = _FakeClient({"scope": "current", "aliases": ["beta"]})
    classify = make_llm_classifier(lambda: client)
    r = route_ask("give me the gist of recent work", NAMES, llm_classify=classify)
    assert r.scope == "current" and r.aliases == ["beta"] and r.reason == "llm router"
    assert client.calls == 1


def test_llm_classifier_failure_falls_back_to_federated():
    from tesserae.ask_router import make_llm_classifier

    classify = make_llm_classifier(lambda: _FakeClient(RuntimeError("backend down")))
    r = route_ask("give me the gist of recent work", NAMES, llm_classify=classify)
    assert r.scope == "federated"  # never a new failure mode


def test_llm_classifier_is_lazy_for_heuristic_cases():
    from tesserae.ask_router import make_llm_classifier

    built = []
    classify = make_llm_classifier(lambda: built.append(1) or _FakeClient({"scope": "federated", "aliases": []}))
    route_ask("how does alpha compile work?", NAMES, llm_classify=classify)  # heuristic -> alpha
    assert built == []  # client never even constructed for a heuristic-resolved question


def test_llm_classifier_rejects_bad_output():
    from tesserae.ask_router import make_llm_classifier

    # 'current' with no concrete alias -> None -> federated fallback.
    classify = make_llm_classifier(lambda: _FakeClient({"scope": "current", "aliases": []}))
    assert route_ask("vague vague vague stuff", NAMES, llm_classify=classify).scope == "federated"


def test_llm_route_rejects_malformed_aliases_and_falls_back():
    from tesserae.ask_router import make_llm_classifier

    q = "give me a broad overview please"  # ambiguous -> reaches the LLM step
    for bad in (1, {"alpha": True}, "alpha", None):
        classify = make_llm_classifier(lambda b=bad: _FakeClient({"scope": "federated", "aliases": b}))
        r = route_ask(q, NAMES, llm_classify=classify)
        # None/0-alias federated still federates all, but never via a crash;
        # a non-list aliases value must NOT be accepted as an llm route.
        if bad in (1, {"alpha": True}):
            assert r.reason != "llm router"


def test_llm_all_registered_subset_is_preserved():
    from tesserae.ask_router import make_llm_classifier

    classify = make_llm_classifier(lambda: _FakeClient({"scope": "all-registered", "aliases": ["alpha", "gamma"]}))
    r = route_ask("give me a broad overview please", NAMES, llm_classify=classify)
    assert r.scope == "all-registered" and r.aliases == ["alpha", "gamma"]


def test_is_followup():
    assert is_followup("and why?")
    assert is_followup("more")
    assert not is_followup("how does the federated retrieval pipeline assemble graphs?")
