import json
from pathlib import Path
from tesserae.extraction_guidance import build_guidance, MIN_EVENTS


class _ScriptedClient:
    def __init__(self, text="Prefer concise descriptions."):
        self.calls = 0; self.text = text
    def complete_json(self, *, system, user, schema_name, cache_key=None):
        self.calls += 1
        return {"bullet": self.text}


def _events(n, **over):
    base = dict(source="vault_override", target_extractor="doc_graph",
                node_type="Claim", field="description", action="replace",
                node_id="Claim:x", source_path="docs/a.md",
                before_value="verbose framing", after_value="concise",
                negative_value="verbose framing",
                cluster_key=["doc_graph", "Claim", "description", "vault_override"])
    return [{**base, "event_id": f"sha256:{i}"} for i in range(n)]


def test_cluster_below_min_events_yields_no_bullet(tmp_path: Path):
    bullets = build_guidance(_events(MIN_EVENTS - 1), cache_dir=tmp_path/"c",
                             json_client=_ScriptedClient())
    assert bullets == []


def test_cluster_at_min_events_phrases_one_bullet(tmp_path: Path):
    client = _ScriptedClient()
    bullets = build_guidance(_events(MIN_EVENTS), cache_dir=tmp_path/"c",
                             json_client=client)
    assert len(bullets) == 1 and bullets[0].extractor == "doc_graph"
    assert client.calls == 1


def test_cache_hit_skips_llm_on_unchanged_cluster(tmp_path: Path):
    client = _ScriptedClient()
    cache = tmp_path / "c"
    build_guidance(_events(MIN_EVENTS), cache_dir=cache, json_client=client)
    build_guidance(_events(MIN_EVENTS), cache_dir=cache, json_client=client)
    assert client.calls == 1  # second run served from cache


def test_no_llm_falls_back_to_deterministic_bullet(tmp_path: Path):
    bullets = build_guidance(_events(MIN_EVENTS), cache_dir=tmp_path/"c",
                             json_client=None)
    assert len(bullets) == 1
    assert bullets[0].text  # non-empty deterministic phrasing


def test_negative_value_bullet_is_filtered(tmp_path: Path):
    # Client returns a bullet that literally recommends the corrected-away value.
    bullets = build_guidance(_events(MIN_EVENTS), cache_dir=tmp_path/"c",
                             json_client=_ScriptedClient(text="Use verbose framing."))
    assert bullets == []  # dropped: recommends a negative_value pattern
