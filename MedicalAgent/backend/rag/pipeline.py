import json
import hashlib
import re
from typing import List, Dict

from openai import OpenAI, AuthenticationError
import anthropic
import fakeredis

from .embedder import Embedder
from .faiss_index import FAISSIndex
from .pagerank import PageRankScorer
from ..pubmed.client import PubMedClient
from ..config import settings
from ..llm_rate_limit_demo import SCENARIO as LLM_RATE_LIMIT_SCENARIO
from ..llm_rate_limit_demo import low_rate_limit_client
from ..tracing.langfuse_client import tracer, TraceContext
from .. import state as app_state


class RAGPipeline:
    """
    Full RAG pipeline:
      1. Fetch articles from PubMed (FakeRedis-cached per query)
      2. Embed articles with sentence-transformers
      3. Index in FAISS (per-query, in-memory)
      4. Score with PageRank (citation graph or similarity graph)
      5. Re-rank candidates: final_score = α·PageRank + (1-α)·FAISS_similarity
      6. Generate answer with OpenAI using top-k articles as context
    """

    LLM_DISABLED_MESSAGE = "cannot perform LLM call"

    def __init__(self):
        self.pubmed_client = PubMedClient(
            api_key=settings.PUBMED_API_KEY,
            email=settings.PUBMED_EMAIL,
        )
        print("Initializing embedder (may download model on first run)...")
        self.embedder = Embedder(settings.EMBEDDING_MODEL)
        self.pagerank_scorer = PageRankScorer()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None

    def _cache_key(self, query: str, max_results: int) -> str:
        key = f"{query.lower().strip()}:{max_results}"
        return f"pubmed:{hashlib.md5(key.encode()).hexdigest()}"

    def _fetch_articles(self, query: str, max_results: int) -> List[Dict]:
        cache_key = self._cache_key(query, max_results)
        cached = self.redis.get(cache_key)
        if cached:
            print(f"[Cache HIT] {query}")
            return json.loads(cached)

        print(f"[PubMed] Searching: {query}")
        pmids = self.pubmed_client.search(query, max_results)
        if not pmids:
            return []

        articles = self.pubmed_client.fetch_details(pmids)
        if articles:
            self.redis.setex(cache_key, 3600, json.dumps(articles))
        return articles

    def _has_special_character_demo_trigger(self, query: str) -> bool:
        if not settings.SPECIAL_CHARACTER_DEMO_ERROR:
            return False

        trigger_chars = re.escape(settings.SPECIAL_CHARACTER_DEMO_CHARS)
        return bool(re.search(f"[{trigger_chars}]", query or ""))

    def _normalize_user_query(self, query: str) -> str:
        if not query:
            return ""

        # Remove configured unsupported characters and normalize whitespace.
        trigger_chars = re.escape(settings.SPECIAL_CHARACTER_DEMO_CHARS)
        cleaned = re.sub(f"[{trigger_chars}]", " ", query)
        return re.sub(r"\s+", " ", cleaned).strip()

    def query(
        self,
        user_query: str,
        max_articles: int = 30,
        top_k: int = 5,
        trace_ctx: TraceContext = None,
        scenario: str | None = None,
    ) -> Dict:
        ctx = trace_ctx  # may be None if called without a trace

        def _start(name, inp=None):
            if ctx:
                ctx.start_step(name, inp)

        def _end(name, out=None):
            if ctx:
                ctx.end_step(name, out)

        if self._has_special_character_demo_trigger(user_query):
            _start("query_validation", {"query": (user_query or "")[:120]})
            raise RuntimeError("Query preprocessing failed during user input normalization.")

        if not (user_query or "").strip():
            raise ValueError("Query cannot be empty after removing unsupported special characters.")

        if scenario == LLM_RATE_LIMIT_SCENARIO:
            _start("openai_generation", {
                "scenario": LLM_RATE_LIMIT_SCENARIO,
                "deployment": low_rate_limit_client.deployment,
                "model": low_rate_limit_client.model,
                "limit_per_minute": low_rate_limit_client.limit,
            })
            llm_result = low_rate_limit_client.call(user_query)
            answer = llm_result["answer"]
            _end("openai_generation", {
                "scenario": LLM_RATE_LIMIT_SCENARIO,
                "deployment": low_rate_limit_client.deployment,
                "model": low_rate_limit_client.model,
                "current_window_hits": llm_result["current_window_hits"],
                "limit_per_minute": llm_result["limit_per_minute"],
                "remaining": llm_result["remaining"],
                "answer_length": len(answer),
            })
            return {
                "answer": answer,
                "sources": [],
                "total_fetched": 0,
                "pagerank_method": "llm-rate-limit-azure",
                "scenario": LLM_RATE_LIMIT_SCENARIO,
                "deployment": low_rate_limit_client.deployment,
                "model": low_rate_limit_client.model,
                "current_window_hits": llm_result["current_window_hits"],
                "limit_per_minute": llm_result["limit_per_minute"],
                "remaining": llm_result["remaining"],
            }

        if not app_state.llm_enabled:
            _start("openai_generation", {
                "model": "disabled",
                "reason": "LLM access disabled by admin",
            })
            disabled_attempts = app_state.record_llm_disabled_attempt()
            if disabled_attempts >= app_state.LLM_DISABLED_ERROR_THRESHOLD:
                raise RuntimeError(
                    f"{self.LLM_DISABLED_MESSAGE}. "
                    "3 user queries were attempted within 10 minutes."
                )
            answer = self.LLM_DISABLED_MESSAGE
            _end("openai_generation", {
                "answer_length": len(answer),
                "disabled_attempts": disabled_attempts,
            })
            return {
                "answer": answer,
                "sources": [],
                "total_fetched": 0,
                "pagerank_method": "n/a",
            }

        # --- Step 1: Fetch articles ---
        _start("pubmed_fetch", {"query": user_query, "max_articles": max_articles})
        articles = self._fetch_articles(user_query, max_articles)
        articles = [a for a in articles if len(a.get("abstract", "")) > 50]
        _end("pubmed_fetch", {"articles_count": len(articles), "cached": ctx is not None})

        if not articles:
            return {
                "answer": (
                    "No relevant articles with abstracts found on PubMed for your query. "
                    "Try different or broader medical keywords."
                ),
                "sources": [],
                "total_fetched": 0,
                "pagerank_method": "n/a",
            }

        # --- Step 2: Embed articles ---
        print(f"[Embedding] {len(articles)} articles...")
        _start("embedding", {"articles_count": len(articles), "model": settings.EMBEDDING_MODEL})
        texts = [f"{a['title']}. {a['abstract']}" for a in articles]
        embeddings = self.embedder.embed(texts)
        _end("embedding", {"dimension": embeddings.shape[1] if len(embeddings) else 0})

        # --- Step 3: Build FAISS index ---
        faiss_index = FAISSIndex(self.embedder.dimension)
        faiss_index.add(articles, embeddings)

        # --- Step 4: PageRank scoring ---
        pagerank_method = "similarity"
        _start("pagerank", {"mode": "citation"})
        try:
            pmids = [a["pmid"] for a in articles[:20]]
            links = self.pubmed_client.fetch_links(pmids)
            citation_scores = self.pagerank_scorer.compute_from_citation_links(
                articles, links
            )
            if citation_scores:
                pagerank_scores = citation_scores
                pagerank_method = "citation"
                print(f"[PageRank] Citation-based ({sum(len(v) for v in links.values())} links)")
            else:
                raise ValueError("No citation links found")
        except Exception:
            pagerank_scores = self.pagerank_scorer.compute_from_embeddings(
                articles, embeddings
            )
            pagerank_method = "similarity"
            print("[PageRank] Similarity-based (fallback)")
        _end("pagerank", {"method": pagerank_method})

        for article in articles:
            article["pagerank_score"] = pagerank_scores.get(article["pmid"], 0.0)

        # --- Step 5: FAISS retrieval + re-ranking ---
        _start("faiss_retrieval", {"top_k": top_k, "query": user_query[:80]})
        query_embedding = self.embedder.embed([user_query])[0]
        num_candidates = min(top_k * 4, len(articles))
        candidates = faiss_index.search(query_embedding, k=num_candidates)

        if not candidates:
            return {
                "answer": "Failed to retrieve candidates. Please retry.",
                "sources": [],
                "total_fetched": len(articles),
                "pagerank_method": pagerank_method,
            }

        # Normalize scores
        max_pr = max(c.get("pagerank_score", 0) for c in candidates) or 1.0
        min_fs = min(c.get("faiss_score", 0) for c in candidates)
        max_fs = max(c.get("faiss_score", 0) for c in candidates)
        fs_range = (max_fs - min_fs) or 1.0

        alpha = settings.PAGERANK_ALPHA
        for c in candidates:
            c["pagerank_norm"] = c.get("pagerank_score", 0) / max_pr
            c["faiss_norm"] = (c.get("faiss_score", 0) - min_fs) / fs_range
            c["final_score"] = alpha * c["pagerank_norm"] + (1 - alpha) * c["faiss_norm"]

        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        top_articles = candidates[:top_k]
        _end("faiss_retrieval", {
            "candidates_evaluated": len(candidates),
            "top_k_returned": len(top_articles),
            "top_score": round(top_articles[0].get("final_score", 0), 4) if top_articles else 0,
        })

        # --- Step 6: Generate answer (Anthropic preferred, OpenAI fallback) ---
        use_anthropic = bool(settings.ANTHROPIC_API_KEY)
        model_used = settings.ANTHROPIC_MODEL if use_anthropic else settings.OPENAI_MODEL
        print(f"[{'Anthropic' if use_anthropic else 'OpenAI'}] Generating answer from {len(top_articles)} articles...")
        _start("openai_generation", {"model": model_used, "articles_used": len(top_articles)})
        answer = self._generate_answer(user_query, top_articles, ctx)
        _end("openai_generation", {"answer_length": len(answer)})

        result = {
            "answer": answer,
            "sources": [
                {
                    "pmid": a["pmid"],
                    "title": a["title"],
                    "authors": a.get("authors", []),
                    "journal": a.get("journal", ""),
                    "year": a.get("year", ""),
                    "url": a.get("url", ""),
                    "abstract_preview": (
                        a.get("abstract", "")[:350] + "..."
                        if len(a.get("abstract", "")) > 350
                        else a.get("abstract", "")
                    ),
                    "scores": {
                        "final": round(a.get("final_score", 0), 4),
                        "pagerank": round(a.get("pagerank_norm", 0), 4),
                        "similarity": round(a.get("faiss_norm", 0), 4),
                    },
                }
                for a in top_articles
            ],
            "total_fetched": len(articles),
            "pagerank_method": pagerank_method,
        }
        return result

    @staticmethod
    def _clean(text: str) -> str:
        """
        Strip characters that cause OpenAI's JSON parser to reject the request body:
        - Null bytes and other ASCII control chars (except tab/newline/CR)
        - Lone Unicode surrogates produced by some XML parsers
        """
        import re
        if not text:
            return text
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        # Re-encode through UTF-8 to drop lone surrogates
        text = text.encode('utf-8', errors='replace').decode('utf-8')
        return text

    def _build_context(self, articles: List[Dict]) -> str:
        parts = []
        for i, a in enumerate(articles, 1):
            authors = a.get("authors", [])
            authors_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
            parts.append(
                f"[{i}] {self._clean(a['title'])}\n"
                f"    Authors: {authors_str or 'N/A'} | "
                f"{self._clean(a.get('journal', 'N/A'))} ({a.get('year', 'N/A')})\n"
                f"    Abstract: {self._clean(a.get('abstract', ''))[:600]}"
            )

        return "\n\n".join(parts)

    def _generate_answer_anthropic(self, query: str, articles: List[Dict], ctx: TraceContext = None) -> str:
        if not self.anthropic_client:
            raise RuntimeError("Anthropic client not configured")

        if not app_state.llm_enabled:
            return self.LLM_DISABLED_MESSAGE

        context = self._build_context(articles)

        system_prompt = (
            "You are a helpful medical research assistant. "
            "Use ONLY the provided PubMed context. "
            "If evidence is insufficient, say so explicitly. "
            "Be concise, accurate, and include key study findings."
        )

        user_content = (
            f"PubMed Context:\n\n{context}\n\n---\n\n"
            f"Question: {self._clean(query)}\n\n"
            "Answer with:\n"
            "1) Direct answer\n"
            "2) Supporting evidence from the studies\n"
            "3) Any caveats/limitations"
        )

        try:
            response = self.anthropic_client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=700,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_content}
                ],
                temperature=0.2,
            )

            parts = getattr(response, "content", []) or []
            text_chunks = [getattr(p, "text", "") for p in parts if getattr(p, "type", "") == "text"]
            answer = "\n".join([t for t in text_chunks if t]).strip()
            return answer or "I could not generate a response at the moment."

        except Exception as e:
            print(f"[Anthropic Error] {e}")
            return ""

    def _generate_answer_openai(self, query: str, articles: List[Dict], ctx: TraceContext = None) -> str:
        if not app_state.llm_enabled:
            return self.LLM_DISABLED_MESSAGE

        context = self._build_context(articles)

        system_prompt = (
            "You are a helpful medical research assistant. "
            "Use ONLY the provided PubMed context. "
            "If evidence is insufficient, say so explicitly. "
            "Be concise, accurate, and include key study findings."
        )

        user_content = f"PubMed Context:\n\n{context}\n\n---\n\nQuestion: {self._clean(query)}"

        try:
            response = self.openai_client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": self._clean(system_prompt)},
                    {"role": "user", "content": self._clean(user_content)}
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except AuthenticationError as e:
            # Keep auth failures explicit for demo/debugging
            return f"OpenAI authentication failed: {e}"
        except Exception as e:
            print(f"[OpenAI Error] {e}")
            return "Failed to generate answer due to API issue. Please retry."

    def _generate_answer(self, query: str, articles: List[Dict], ctx: TraceContext = None) -> str:
        """Prefer Anthropic when configured; otherwise fallback to OpenAI."""
        if self.anthropic_client:
            return self._generate_answer_anthropic(query, articles, ctx)
        return self._generate_answer_openai(query, articles, ctx)
