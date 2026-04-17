import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.rag.pipeline import RAGPipeline


def test_query_normalizes_special_character_demo_input():
    pipeline = RAGPipeline.__new__(RAGPipeline)
    captured = {}

    def fake_fetch(query, max_results):
        captured["query"] = query
        return []

    pipeline._fetch_articles = fake_fetch

    result = pipeline.query("Diabete$", max_articles=5, top_k=2)

    assert captured["query"] == "Diabete"
    assert result["total_fetched"] == 0
    assert "No relevant articles" in result["answer"]


def test_query_allows_plain_input_before_fetching_articles():
    pipeline = RAGPipeline.__new__(RAGPipeline)
    captured = {}

    def fake_fetch(query, max_results):
        captured["query"] = query
        return []

    pipeline._fetch_articles = fake_fetch

    result = pipeline.query("Diabete", max_articles=5, top_k=2)

    assert captured["query"] == "Diabete"
    assert result["total_fetched"] == 0
    assert "No relevant articles" in result["answer"]
