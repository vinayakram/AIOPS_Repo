import networkx as nx
import numpy as np
from typing import List, Dict
from sklearn.metrics.pairwise import cosine_similarity


class PageRankScorer:
    """
    PageIndex-based ranking system for sample articles.

    Two modes:
    1. Citation-based: Uses PubMed related-article links to build a directed graph.
       Articles highly cited/linked by other relevant articles get higher scores.
    2. Similarity-based (fallback): Builds a weighted graph from embedding cosine
       similarity. Articles semantically central to the topic score higher.

    In both cases, PageRank identifies the most "authoritative" articles
    in the retrieved set, which is then blended with FAISS similarity for final ranking.
    """

    def __init__(self, damping_factor: float = 0.85, similarity_threshold: float = 0.5):
        self.damping_factor = damping_factor
        self.similarity_threshold = similarity_threshold

    def compute_from_citation_links(
        self,
        articles: List[Dict],
        links: Dict[str, List[str]],
    ) -> Dict[str, float]:
        """
        Build a directed citation graph and compute PageRank.
        Returns empty dict if no citation links exist (triggers fallback).
        """
        G = nx.DiGraph()
        pmid_set = {a["pmid"] for a in articles}

        for article in articles:
            G.add_node(article["pmid"])

        edge_count = 0
        for source_pmid, linked_pmids in links.items():
            if source_pmid in pmid_set:
                for target_pmid in linked_pmids:
                    if target_pmid in pmid_set and target_pmid != source_pmid:
                        G.add_edge(source_pmid, target_pmid)
                        edge_count += 1

        if edge_count == 0:
            return {}

        return self._run_pagerank(G)

    def compute_from_embeddings(
        self,
        articles: List[Dict],
        embeddings: np.ndarray,
    ) -> Dict[str, float]:
        """
        Build a weighted undirected similarity graph from embeddings and compute PageRank.
        Articles that are semantically similar to many other retrieved articles
        are considered more central/authoritative for the topic.
        """
        G = nx.DiGraph()

        if len(articles) == 0:
            return {}
        if len(articles) == 1:
            return {articles[0]["pmid"]: 1.0}

        for article in articles:
            G.add_node(article["pmid"])

        sim_matrix = cosine_similarity(embeddings)
        n = len(articles)

        for i in range(n):
            for j in range(n):
                if i != j and sim_matrix[i][j] >= self.similarity_threshold:
                    G.add_edge(
                        articles[i]["pmid"],
                        articles[j]["pmid"],
                        weight=float(sim_matrix[i][j]),
                    )

        if G.number_of_edges() == 0:
            # All articles are equally important (none are highly similar)
            return {a["pmid"]: 1.0 / n for a in articles}

        return self._run_pagerank(G)

    def _run_pagerank(self, G: nx.DiGraph) -> Dict[str, float]:
        try:
            return nx.pagerank(
                G,
                alpha=self.damping_factor,
                max_iter=200,
                tol=1e-6,
                weight="weight",
            )
        except nx.PowerIterationFailedConvergence:
            n = len(G)
            return {node: 1.0 / n for node in G.nodes()} if n > 0 else {}
