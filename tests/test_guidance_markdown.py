from tesserae.guidance_markdown import GuidanceBullet, render_guidance, parse_guidance, slice_guidance


def _bullets():
    return [
        GuidanceBullet(extractor="doc_graph", node_type="Claim",
                       cluster_hash="sha256:abc", source="vault_override",
                       field="description", events=7,
                       text="Prefer concise claim descriptions; omit broad framing."),
        GuidanceBullet(extractor="session_findings", node_type="SessionDecision",
                       cluster_hash="sha256:def", source="vault_override",
                       field="body", events=4,
                       text="Phrase decisions as accepted choices, not next steps."),
    ]


def test_render_parse_roundtrip():
    md = render_guidance(_bullets())
    parsed = parse_guidance(md)
    assert {b.text for b in parsed} == {b.text for b in _bullets()}
    assert {b.extractor for b in parsed} == {"doc_graph", "session_findings"}
    assert any(b.events == 7 for b in parsed)


def test_slice_returns_only_matching_extractor_and_type():
    md = render_guidance(_bullets())
    parsed = parse_guidance(md)
    sliced = slice_guidance(parsed, extractor="doc_graph", node_types={"Claim", "Dataset"})
    assert len(sliced) == 1 and sliced[0].node_type == "Claim"
    assert slice_guidance(parsed, extractor="doc_graph", node_types={"Dataset"}) == []


def test_user_deleted_bullet_stays_deleted():
    md = render_guidance(_bullets())
    # Simulate the user deleting the SessionDecision bullet line.
    kept = "\n".join(l for l in md.splitlines() if "accepted choices" not in l)
    parsed = parse_guidance(kept)
    assert all("accepted choices" not in b.text for b in parsed)
