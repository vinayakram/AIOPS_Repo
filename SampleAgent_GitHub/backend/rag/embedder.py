from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.dimension = self.model.get_sentence_embedding_dimension()
        print(f"Embedding model loaded. Dimension: {self.dimension}")

    def embed(self, texts: List[str]) -> np.ndarray:
        """Embed a list of texts, returning L2-normalized float32 vectors."""
        if not texts:
            return np.array([])
        embeddings = self.model.encode(
            texts,
            show_progress_bar=False,
            normalize_embeddings=True,  # L2-normalize for cosine similarity via dot product
        )
        return embeddings.astype(np.float32)
