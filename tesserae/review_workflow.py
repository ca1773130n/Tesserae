"""Human-friendly review queue export helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .canonicalization import ReviewQueue, review_band


class ReviewQueueExporter:
    """Render canonicalization review queues for human review.

    JSON remains the machine contract, while markdown/jsonl/template outputs make
    it practical to inspect candidates and produce a decision file without
    memorizing the schema.
    """

    def render_markdown(self, queue: ReviewQueue) -> str:
        lines = ["# Research Graph Review Queue", "", f"pending_items: {len(queue.items)}", ""]
        if not queue.items:
            lines.append("_No pending review items._")
            return "\n".join(lines).rstrip() + "\n"

        # Only emitted when embedding candidates are present, so string-only
        # queues render byte-identically to before. A reviewer working
        # top-down must be told that for `similar_embedding` the score ranks
        # topical proximity, NOT identity.
        if any(item.reason == "similar_embedding" for item in queue.items):
            lines.extend(
                [
                    "> `similar_embedding` scores rank TOPICAL PROXIMITY, not identity:"
                    " version and family siblings (Llama 2 / Llama 3) score highest and"
                    " must never merge. Items are ordered by actionability, not by score;"
                    " see each item's `band`.",
                    "",
                ]
            )

        for idx, item in enumerate(queue.items, start=1):
            _, band_label = review_band(item)
            lines.extend(
                [
                    f"## {idx}. {item.left_name} ↔ {item.right_name}",
                    "",
                    f"- item_id: `{item.id}`",
                    f"- node_type: `{item.node_type}`",
                    f"- reason: `{item.reason}`",
                    f"- score: {item.score}",
                ]
                + ([f"- band: {band_label}"] if band_label else [])
                + [
                    f"- left_node_id: `{item.left_node_id}`",
                    f"- right_node_id: `{item.right_node_id}`",
                    "",
                    "Decision template:",
                    "",
                    "```json",
                    json.dumps(
                        {
                            "item_id": item.id,
                            "action": "TODO: merge|keep_separate",
                            "canonical_node_id": item.left_node_id,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def render_jsonl(self, queue: ReviewQueue) -> str:
        return "".join(json.dumps({"item_id": item.id, **item.model_dump()}, ensure_ascii=False, sort_keys=True) + "\n" for item in queue.items)

    def render_decision_template(self, queue: ReviewQueue) -> str:
        decisions = [
            {
                "item_id": item.id,
                "action": "TODO: merge|keep_separate",
                "canonical_node_id": item.left_node_id,
                "comment": f"Choose {item.left_name} or {item.right_name} as canonical, or keep_separate.",
            }
            for item in queue.items
        ]
        return json.dumps({"decisions": decisions}, ensure_ascii=False, indent=2) + "\n"

    def write_files(
        self,
        queue: ReviewQueue,
        markdown_path: Optional[str | Path] = None,
        jsonl_path: Optional[str | Path] = None,
        decision_template_path: Optional[str | Path] = None,
    ) -> None:
        if markdown_path:
            path = Path(markdown_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.render_markdown(queue), encoding="utf-8")
        if jsonl_path:
            path = Path(jsonl_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.render_jsonl(queue), encoding="utf-8")
        if decision_template_path:
            path = Path(decision_template_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.render_decision_template(queue), encoding="utf-8")
