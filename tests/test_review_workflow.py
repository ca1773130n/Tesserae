import json

from tesserae.canonicalization import ReviewItem, ReviewQueue
from tesserae.review_workflow import ReviewQueueExporter


def sample_queue():
    return ReviewQueue([
        ReviewItem(
            id="review:similar_name:test",
            left_node_id="MethodologicalConcept:gs:test",
            right_node_id="MethodologicalConcept:4dgs:test",
            left_name="Gaussian Splatting",
            right_name="4D Gaussian Splatting",
            node_type="MethodologicalConcept",
            reason="similar_name",
            score=0.9,
        )
    ])


def test_review_queue_exporter_renders_markdown_jsonl_and_decision_template():
    exporter = ReviewQueueExporter()
    queue = sample_queue()

    markdown = exporter.render_markdown(queue)
    jsonl = exporter.render_jsonl(queue)
    template = exporter.render_decision_template(queue)

    assert "# Research Graph Review Queue" in markdown
    assert "Gaussian Splatting ↔ 4D Gaussian Splatting" in markdown
    assert "canonical_node_id" in markdown
    assert json.loads(jsonl.strip())["item_id"] == "review:similar_name:test"
    payload = json.loads(template)
    assert payload["decisions"][0]["item_id"] == "review:similar_name:test"
    assert payload["decisions"][0]["action"] == "TODO: merge|keep_separate"


def test_review_queue_exporter_writes_requested_files(tmp_path):
    exporter = ReviewQueueExporter()
    queue = sample_queue()

    markdown_path = tmp_path / "review.md"
    jsonl_path = tmp_path / "review.jsonl"
    template_path = tmp_path / "decisions.template.json"
    exporter.write_files(queue, markdown_path=markdown_path, jsonl_path=jsonl_path, decision_template_path=template_path)

    assert markdown_path.read_text(encoding="utf-8").startswith("# Research Graph Review Queue")
    assert jsonl_path.read_text(encoding="utf-8").count("\n") == 1
    assert json.loads(template_path.read_text(encoding="utf-8"))["decisions"]


def _embedding_item(item_id, left, right, score):
    return ReviewItem(
        id=item_id,
        left_node_id=f"Model:{left}:t",
        right_node_id=f"Model:{right}:t",
        left_name=left,
        right_name=right,
        node_type="Model",
        reason="similar_embedding",
        score=score,
    )


def test_semantic_queue_export_warns_about_the_score_inversion_and_labels_bands():
    """The module docstring documents the inversion; the artifact a HUMAN reads
    did not repeat it. A reviewer working top-down was shown the never-merge
    pairs under the most confident-looking numbers, with no warning."""
    queue = ReviewQueue([
        _embedding_item("review:a", "Llama 2", "Llama 3", 0.9896),
        _embedding_item("review:b", "Edwin Aldrin", "Buzz Aldrin", 0.9074),
    ])
    markdown = ReviewQueueExporter().render_markdown(queue)

    assert "TOPICAL PROXIMITY" in markdown
    assert "version/family siblings" in markdown
    assert "candidate duplicates" in markdown


def test_string_similarity_queues_render_unchanged():
    """The band note is additive: a queue with no embedding candidates must not
    grow a warning about a pass that never ran."""
    markdown = ReviewQueueExporter().render_markdown(sample_queue())

    assert "TOPICAL PROXIMITY" not in markdown
    assert "- band:" not in markdown
