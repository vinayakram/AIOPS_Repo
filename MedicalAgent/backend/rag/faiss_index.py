"""
FAISS-based in-memory vector index for the MedicalAgent RAG pipeline using inner-product
similarity. Stores article metadata alongside embeddings and returns top-k results with
cosine similarity scores. The index is rebuilt per query from L2-normalised embeddings
produced by the Embedder class.

MedicalAgent RAGパイプライン向けの内積類似度を使用したFAISSベースのインメモリベクトルインデックス。
論文メタデータをエンベディングと共に格納し、コサイン類似度スコア付きのトップk件の結果を返す。
インデックスはEmbedderクラスが生成するL2正規化済みエンベディングからクエリごとに再構築される。
"""
import faiss
import numpy as np
from typing import List, Dict


class FAISSIndex:
    """
    FAISS index using IndexFlatIP (inner product) for cosine similarity.
    Works with L2-normalized embeddings from the Embedder class.
    Higher scores = more similar (range: 0 to 1 for normalized vectors).
    """

    def __init__(self, dimension: int):
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)
        self.articles: List[Dict] = []

    def add(self, articles: List[Dict], embeddings: np.ndarray):
        """Reset and rebuild the index with new articles and their embeddings."""
        self.index = faiss.IndexFlatIP(self.dimension)
        self.articles = []

        if len(articles) == 0 or len(embeddings) == 0:
            return

        embeddings_f32 = embeddings.astype(np.float32)
        self.index.add(embeddings_f32)
        self.articles = list(articles)

    def search(self, query_embedding: np.ndarray, k: int = 10) -> List[Dict]:
        """
        Find top-k most similar articles to the query embedding.
        Returns articles with an added 'faiss_score' field (cosine similarity).
        """
        if self.index.ntotal == 0:
            return []

        k = min(k, self.index.ntotal)
        query_f32 = query_embedding.astype(np.float32).reshape(1, -1)
        scores, indices = self.index.search(query_f32, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if 0 <= idx < len(self.articles):
                article = self.articles[idx].copy()
                article["faiss_score"] = float(score)  # cosine similarity (0 to 1)
                results.append(article)
        return results
