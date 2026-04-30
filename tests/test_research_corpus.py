from llm_wiki.batch import merge_graphs
from llm_wiki.research_graph import ResearchCorpusAnalyzer, ResearchGraphExtractor, ResearchNodeType


PAPER_DAY_1 = """
# 논문 분석: 2604.00538

> - arxiv: https://arxiv.org/abs/2604.00538
> - 분석일: 2026-04-25

TRiGS: Temporal Rigid-Body Motion for Scalable 4D Gaussian Splatting | Cool Papers - 몰입형 논문 탐색

## 2604.00538

최근 4D Gaussian Splatting, 즉 4DGS 방법들은 동적 장면 재구성에서 인상적인 성능을 달성했다.
TRiGS는 표준 벤치마크에서 높은 충실도의 렌더링을 달성한다.
"""

PAPER_DAY_2 = """
# 논문 분석: 2601.17835

> - arxiv: https://arxiv.org/abs/2601.17835
> - 분석일: 2026-04-26

Geometry-Grounded Gaussian Splatting | Cool Papers - 몰입형 논문 탐색

Gaussian Splatting은 novel view synthesis와 shape reconstruction에서 중요한 방법론이다.
본 논문은 stochastic solid를 사용해 depth map을 개선한다.
"""


def test_extract_title_prefers_real_paper_title_over_analysis_heading_and_arxiv_id():
    graph = ResearchGraphExtractor().extract_text(
        PAPER_DAY_1,
        source_path="data/research/daily/2026-04-25/papers/2604.00538/paper.md",
        source_kind="Paper",
    )

    papers = [node for node in graph.nodes if node.type == ResearchNodeType.PAPER]
    assert len(papers) == 1
    assert papers[0].name == "TRiGS: Temporal Rigid-Body Motion for Scalable 4D Gaussian Splatting"
    assert papers[0].metadata["arxiv_id"] == "2604.00538"
    assert papers[0].metadata["analysis_date"] == "2026-04-25"


def test_paper_file_without_title_falls_back_to_needs_metadata_not_abstract_title():
    """Without a parsable title and no offline cache, F-3 emits ``needs_metadata``.

    The resolver explicitly avoids fabricating a title from the abstract body —
    "MHMO 렌더링" is not promoted to a paper title. ``needs_metadata`` is
    treated as private until the offline arXiv cache resolves the real title.
    """
    graph = ResearchGraphExtractor().extract_text(
        """
# 논문 분석: 2604.02996

> - arxiv: https://arxiv.org/abs/2604.02996
> - 분석일: 2026-04-07

희소 뷰 입력으로부터 상호작용하는 다수의 인간과 객체를 가진 동적 장면을 재구성하는 것은 로봇공학 및 VR/AR용 고정밀 디지털 트윈 생성에 필수적인 중요하면서도 도전적인 과제이다. 우리가 MHMO 렌더링이라고 명명한 이 문제는 두 가지 중요한 장애물을 안고 있다.
""",
        source_path="data/research/daily/2026-04-07/papers/2604.02996/paper.md",
        source_kind="Paper",
    )

    paper = next(node for node in graph.nodes if node.type == ResearchNodeType.PAPER)
    assert paper.name == "arXiv:2604.02996"
    assert paper.metadata["title_quality"] == "needs_metadata"


def test_paper_file_skips_papers_cool_ui_and_rank_markers():
    graph = ResearchGraphExtractor().extract_text(
        """
# 논문 분석: 2602.09022

> - arxiv: https://arxiv.org/abs/2602.09022
> - 분석일: 2026-04-22

## 2602.09022

Total: 1

## #1

# WorldCompass: Reinforcement Learning for Long-Horizon World Models

Authors:
Zehan Wang, Tengfei Wang
""",
        source_path="data/research/daily/2026-04-22/papers/2602.09022/paper.md",
        source_kind="Paper",
    )

    paper = next(node for node in graph.nodes if node.type == ResearchNodeType.PAPER)
    assert paper.name == "WorldCompass: Reinforcement Learning for Long-Horizon World Models"
    assert paper.metadata["title_quality"] == "paper_file"


def test_paper_file_translation_scaffold_does_not_become_title():
    graph = ResearchGraphExtractor().extract_text(
        """
# 논문 분석: 2603.22572

> - arxiv: https://arxiv.org/abs/2603.22572
> - 분석일: 2026-04-07

제공하신 파일을 확인한 결과, **중국어 내용이 포함되어 있지 않습니다**.

의미 있는 내용을 한국어로 번역해 드립니다:

## 2603.22572

### #1 FullCircle: Casual한 360° 촬영으로 손쉬운 3D 재구성

**저자:** Yalda Foroutan, Ipek Oztas
""",
        source_path="data/research/daily/2026-04-07/papers/2603.22572/paper.md",
        source_kind="Paper",
    )

    paper = next(node for node in graph.nodes if node.type == ResearchNodeType.PAPER)
    assert paper.name == "FullCircle: Casual한 360° 촬영으로 손쉬운 3D 재구성"
    assert paper.metadata["title_quality"] == "paper_file"


def test_social_feed_documents_are_ignored_not_promoted_to_research_nodes():
    graph = ResearchGraphExtractor().extract_text(
        """
# Feed

Rendering Multi-Human and Multi-Object with 3D Gaussian Splatting Weiquan Wang, Jun Xiao, Feifei Shao<br>https://arxiv.org/abs/2604.02996
""",
        source_path="data/research/daily/2026-04-07/feeds/20260406122914.md",
        source_kind="SourceDocument",
    )

    assert graph.nodes == []
    assert graph.edges == []


def test_arxiv_references_in_non_research_docs_do_not_create_papers():
    graph = ResearchGraphExtractor().extract_text(
        """
# README

Extract a JSON graph from a paper note:
`python -m llm_wiki data/research/daily/2026-04-26/papers/2601.17835/paper.md`
""",
        source_path="/repo/README.md",
        source_kind="SourceDocument",
    )

    assert not [node for node in graph.nodes if node.type == ResearchNodeType.PAPER]


def test_social_feed_tweets_tldr_and_author_lines_are_not_research_entities():
    extractor = ResearchGraphExtractor()
    graph = extractor.extract_text(
        """
# Feed

RT Tengfei Wang: It's time for their RLHF moment for World Models. We just released #WorldCompass.<br>https://arxiv.org/abs/2602.09022

TL;DR: 10x faster casual capture with clean reconstructions<br>https://arxiv.org/abs/2603.22572

Hierarchical Co-Embedding of Font Shapes and Impression Tags<br><br>Yugo Kubota, Kaito Shiku<br>https://arxiv.org/abs/2604.04158
""",
        source_path="/repo/data/research/daily/2026-04-07/feeds/example.md",
        source_kind="SourceDocument",
    )

    assert graph.nodes == []
    assert graph.edges == []


def test_arxiv_references_in_digest_create_only_unverified_placeholders():
    graph = ResearchGraphExtractor().extract_text(
        """
# Daily Digest

## 본문

Hierarchical Co-Embedding of Font Shapes and Impression Tags<br><br>Yugo Kubota, Kaito Shiku<br>https://arxiv.org/abs/2604.04158 [cs.CV]

Uncertainty-Aware Test-Time Adaptation for Cross-Region Spatio-Temporal Fusion of Land Surface Temperature<br><br>Sofiane Bouaziz<br>https://arxiv.org/abs/2604.04153 [cs.CV]
""",
        source_path="data/research/daily/2026-04-07/digest.md",
        source_kind="SourceDocument",
    )

    papers = {node.metadata.get("arxiv_id"): node for node in graph.nodes if node.type == ResearchNodeType.PAPER}
    assert papers["2604.04158"].name == "arXiv:2604.04158"
    assert papers["2604.04158"].metadata["title_quality"] == "arxiv_only"
    assert papers["2604.04153"].name == "arXiv:2604.04153"
    assert "arXiv:2604.04158" in papers["2604.04158"].aliases


def test_batch_merge_prefers_human_paper_title_over_arxiv_placeholder():
    extractor = ResearchGraphExtractor()
    digest_graph = extractor.extract_text(
        """
# Daily Digest

Related paper: https://arxiv.org/abs/2604.00538
""",
        source_path="data/research/daily/2026-04-25/index.md",
        source_kind="SourceDocument",
    )
    paper_graph = extractor.extract_text(
        PAPER_DAY_1,
        source_path="data/research/daily/2026-04-25/papers/2604.00538/paper.md",
        source_kind="Paper",
    )

    merged = merge_graphs([paper_graph, digest_graph])

    paper = next(node for node in merged.nodes if node.type == ResearchNodeType.PAPER and node.metadata.get("arxiv_id") == "2604.00538")
    assert paper.name == "TRiGS: Temporal Rigid-Body Motion for Scalable 4D Gaussian Splatting"
    assert "arXiv:2604.00538" in paper.aliases


def test_corpus_analyzer_creates_trend_nodes_for_repeated_concepts_across_dates():
    extractor = ResearchGraphExtractor()
    graphs = [
        extractor.extract_text(
            PAPER_DAY_1,
            source_path="data/research/daily/2026-04-25/papers/2604.00538/paper.md",
            source_kind="Paper",
        ),
        extractor.extract_text(
            PAPER_DAY_2,
            source_path="data/research/daily/2026-04-26/papers/2601.17835/paper.md",
            source_kind="Paper",
        ),
    ]

    corpus = ResearchCorpusAnalyzer().summarize_trends(graphs, min_sources=2)

    trend_nodes = [node for node in corpus.nodes if node.type == ResearchNodeType.TREND]
    assert any(node.name == "Trend: Gaussian Splatting" for node in trend_nodes)

    gaussian = next(node for node in corpus.nodes if node.name == "Gaussian Splatting")
    trend = next(node for node in trend_nodes if node.name == "Trend: Gaussian Splatting")
    assert any(edge.source == gaussian.id and edge.target == trend.id and edge.type == "rising_in" for edge in corpus.edges)
    assert trend.metadata["source_count"] == 2
    assert trend.metadata["first_seen"] == "2026-04-25"
    assert trend.metadata["last_seen"] == "2026-04-26"


def test_paper_file_scaffold_apology_does_not_become_public_title():
    graph = ResearchGraphExtractor().extract_text(
        """
# 논문 분석: 2410.17897

> - arxiv: https://arxiv.org/abs/2410.17897
> - 분석일: 2026-04-07

`paper_prompt.txt` 파일에 중국어 논문 분석 내용이 없습니다. 이 파일은 영문 웹페이지 스크랩(Cool Papers 사이트의 "Value Residual Learning" 논문 정보)입니다.
""",
        source_path="data/research/daily/2026-04-07/papers/2410.17897/paper.md",
        source_kind="Paper",
    )

    paper = next(node for node in graph.nodes if node.type == ResearchNodeType.PAPER)
    assert paper.name == "arXiv:2410.17897"
    assert paper.metadata["title_quality"] == "needs_metadata"


def test_paper_file_translation_intro_does_not_become_public_title():
    graph = ResearchGraphExtractor().extract_text(
        """
# 논문 분석: 2604.12345

> - arxiv: https://arxiv.org/abs/2604.12345

제공된 원문은 중국어가 아니라 영어입니다. 아래는 한국어 번역입니다.
""",
        source_path="data/research/daily/2026-04-07/papers/2604.12345/paper.md",
        source_kind="Paper",
    )

    paper = next(node for node in graph.nodes if node.type == ResearchNodeType.PAPER)
    assert paper.name == "arXiv:2604.12345"
    assert paper.metadata["title_quality"] == "needs_metadata"
