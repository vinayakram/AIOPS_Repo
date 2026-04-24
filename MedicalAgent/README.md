# Sample Agent Agent

> Evidence-based sample research assistant — PubMed article retrieval, PageRank re-ranking, FAISS vector search, and Claude/GPT-4o answer generation with JWT-secured user authentication.

---

## Overview

The Sample Agent Agent answers clinical and sample research questions by:

1. **Fetching** up to 50 relevant articles from PubMed via the Entrez API
2. **Embedding** abstracts using `sentence-transformers/all-MiniLM-L6-v2` into a FAISS index
3. **Re-ranking** with a PageRank algorithm over article citation similarity
4. **Generating** an evidence-based answer with inline citations using Claude or OpenAI
5. **Tracing** every query to Langfuse and [AIops Telemetry](https://github.com/kannan-prodapt/AIopsTelemetry)

---

## Features

- **Semantic search** — FAISS cosine-similarity retrieval over PubMed abstracts
- **PageRank re-ranking** — two modes: citation-based (uses PubMed MeSH links) and similarity-based (FAISS similarity graph)
- **LLM answer generation** — Claude Sonnet (primary) or GPT-4o (fallback) with source citations `[1]`, `[2]`…
- **JWT authentication** — register / login flow; all query endpoints require a Bearer token
- **Query history dashboard** — per-user trace log with latency, article counts, and Langfuse links
- **Langfuse tracing** — full trace with pubmed_fetch, embedding, pagerank, faiss_retrieval, openai_generation spans
- **AIops telemetry** — traces forwarded to AIops Telemetry for NFR monitoring and root-cause analysis

---

## Architecture

```
Browser / Client
       │  JWT Bearer token
       ▼
FastAPI  :8000  (backend/main.py)
  ├── /api/query  ─────────────────────────────────────────────────────────┐
  │         │                                                               │
  │         ▼                                                               │
  │   RAGPipeline.query()  (backend/rag/pipeline.py)                       │
  │     ├── PubMedClient.fetch_articles(query, max=30)                     │
  │     ├── Embedder.embed(abstracts)  → FAISS index                       │
  │     ├── PageRank.rank(articles, method="similarity"|"citation")        │
  │     ├── FAISSRetrieval.search(query_embedding, top_k=5)                │
  │     └── LLM.generate(context_articles, query)  → answer + citations   │
  │                                                                         │
  ├── Langfuse tracer  (span per pipeline stage)                           │
  └── AIops client  (trace forwarded to AIops Telemetry :7000) ◄───────────┘
  │
  ├── /  /register  /chat  /dashboard   (frontend HTML pages)
  └── /static                           (CSS + JS)
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- OpenAI API key (required for answer generation if Anthropic key not set)
- Anthropic API key (optional — used as primary LLM)
- [AIops Telemetry](https://github.com/kannan-prodapt/AIopsTelemetry) on port 7000 (optional)

### Install

```bash
git clone https://github.com/kannan-prodapt/SampleAgent.git
cd SampleAgent

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env — set OPENAI_API_KEY or ANTHROPIC_API_KEY, JWT_SECRET_KEY
```

### Run

```bash
python run.py
# → http://0.0.0.0:8000
```

Open **http://localhost:8000** — you'll see the login page. Register a new account, then start querying.

### Docker Pod Threshold Demo

The Docker image includes pod resource guard thresholds as environment variables:

```bash
cd SampleAgent
docker compose up -d --build
```

The compose service limits the demo pod to `0.50` CPU and `1g` memory. When the
SampleAgent pod crosses `POD_CPU_THRESHOLD_PERCENT` or
`POD_MEMORY_THRESHOLD_PERCENT`, normal app access returns HTTP `503` with
`application is not reachable`. `/metrics` and `/api/health` stay available so
Prometheus and operators can still observe it.

To safely simulate the scenario without stressing the VM, run the bounded load
inside the CPU-limited container:

```bash
./scripts/test_pod_cpu_threshold.sh
```

After 3 breaches in 5 minutes, AIops Telemetry raises `NFR-33` for
`nfr_pod_resource_threshold_breach`. RCA should use the last 5 minutes of
Langfuse/Prometheus evidence and recommend changing the pod threshold config
(`POD_CPU_THRESHOLD_PERCENT` / `POD_MEMORY_THRESHOLD_PERCENT`) before redeploy.

The demo CPU threshold is configured at `POD_CPU_THRESHOLD_PERCENT=90`.

Prometheus can use the same target/label assignment shown in the Prometheus UI:

```yaml
scrape_configs:
  - job_name: "sample-agent"
    metrics_path: /metrics
    static_configs:
      - targets:
          - "10.169.91.16:8002"
        labels:
          app: "sample-agent"
```

The same example is saved as `prometheus.yml`. With that assignment,
Prometheus shows `job="sample-agent"`, `app="sample-agent"`, and
`instance="10.169.91.16:8002"` for the `/metrics` endpoint.

---

## Project Structure

```
SampleAgent/
├── run.py                          # Entry point (uvicorn)
├── requirements.txt
├── .env.example
│
├── backend/
│   ├── main.py                     # FastAPI app, lifespan, /api/query, /api/traces
│   ├── config.py                   # Pydantic settings
│   ├── auth/
│   │   ├── routes.py               # POST /api/auth/register, /api/auth/login
│   │   ├── jwt_handler.py          # JWT encode/decode, get_current_user
│   │   └── password_handler.py     # bcrypt hash/verify
│   ├── database/
│   │   └── models.py               # SQLAlchemy User + TraceLog models
│   ├── pubmed/
│   │   └── client.py               # Entrez API fetch, parse XML, return Article list
│   ├── rag/
│   │   ├── pipeline.py             # Orchestrates the full RAG flow
│   │   ├── embedder.py             # sentence-transformers embeddings
│   │   ├── faiss_index.py          # FAISS index build + similarity search
│   │   └── pagerank.py             # Citation and similarity PageRank
│   └── tracing/
│       ├── langfuse_client.py      # Langfuse trace/span helpers
│       └── aiops_client.py         # Forwards completed trace to AIops Telemetry
│
└── frontend/
    ├── index.html                  # Login page
    ├── register.html               # Registration page
    ├── chat.html                   # Query interface
    ├── dashboard.html              # Trace history dashboard
    └── static/
        ├── css/styles.css
        └── js/app.js               # sendQuery(), auth guard, source cards
```

---

## RAG Pipeline

### 1 — PubMed Fetch

Calls NCBI Entrez `esearch` + `efetch` APIs to retrieve up to `max_articles` (default 30) PubMed records matching the query. Returns structured `Article` objects with title, authors, abstract, journal, year, PMID, and URL.

### 2 — Embedding

Encodes all article abstracts using `sentence-transformers/all-MiniLM-L6-v2` (384-dim). Model is loaded once at startup.

### 3 — FAISS Index

Builds an in-memory FAISS flat index (cosine similarity via L2 on normalised vectors) over the embedded abstracts. Retrieves top-K most similar to the query embedding.

### 4 — PageRank Re-ranking

Two modes selected automatically:

| Mode | Used when | Description |
|---|---|---|
| `citation` | Citation data available | Builds directed citation graph; applies standard PageRank |
| `similarity` | Default | Builds similarity graph from FAISS pairwise distances; applies PageRank |

Final score = weighted combination of PageRank score + FAISS similarity score.

### 5 — Answer Generation

Top-K articles (title + abstract) are passed as context to Claude Sonnet or GPT-4o with a system prompt instructing it to generate an evidence-based answer with `[1]`, `[2]`… inline citations. The model falls back gracefully if the primary key is unavailable.

---

## API

### Authentication

```http
POST /api/auth/register
Content-Type: application/json

{ "username": "alice", "password": "secret123" }
```

```http
POST /api/auth/login
Content-Type: application/json

{ "username": "alice", "password": "secret123" }
→ { "access_token": "eyJ...", "token_type": "bearer" }
```

### Query

```http
POST /api/query
Authorization: Bearer <token>
Content-Type: application/json

{
  "query": "Alzheimer's disease treatment options",
  "max_articles": 30,
  "top_k": 5
}
```

**Response:**
```json
{
  "answer": "Current treatments for Alzheimer's include... [1][2]",
  "sources": [
    {
      "pmid": "12345678",
      "title": "Cholinesterase inhibitors in Alzheimer's disease",
      "authors": ["Smith J", "Doe A"],
      "journal": "NEJM",
      "year": 2024,
      "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
      "abstract_preview": "...",
      "scores": { "final": 0.87, "pagerank": 0.72, "similarity": 0.91 }
    }
  ],
  "total_fetched": 30,
  "pagerank_method": "similarity",
  "sources_count": 5,
  "trace_id": "abc-123",
  "langfuse_url": "https://cloud.langfuse.com/trace/abc-123"
}
```

### Trace History

```http
GET /api/traces?limit=50
Authorization: Bearer <token>
```

```http
GET /api/traces/stats
Authorization: Bearer <token>
```

---

## Environment Variables

```env
# LLM (at least one required)
ANTHROPIC_API_KEY=                  # primary — Claude Sonnet
OPENAI_API_KEY=changeme               # fallback / required if Anthropic not set

# Auth
JWT_SECRET_KEY=change-me-in-production
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440    # 24 hours

# Database
DATABASE_URL=sqlite:///./sample_agent.db

# Langfuse (optional)
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# AIops Telemetry (optional)
AIOPS_SERVER_URL=http://localhost:7000
AIOPS_APP_NAME=sample-agent
AIOPS_API_KEY=
```

---

## Frontend Pages

| URL | Page | Description |
|---|---|---|
| `/` | Login | Username + password login form |
| `/register` | Register | New user registration |
| `/chat` | Chat | Query interface with source cards, score bars, abstract toggle |
| `/dashboard` | Dashboard | Query history with latency, article counts, Langfuse links |

---

## Integration with AIops Telemetry

Every completed query is forwarded to AIops Telemetry with:
- A root trace (`sample-agent-query`)
- Child spans: `pubmed_fetch`, `embedding`, `pagerank`, `faiss_retrieval`, `openai_generation`
- Token counts on the LLM span
- Error details if any stage fails

AIops Telemetry's NFR-29 rule specifically detects when the answer body contains `"⚠️ Error generating response"` — catching cases where the LLM API fails silently and the error is returned inside the response rather than as an HTTP error code.

---

## Related Projects

- [AIopsTelemetry](https://github.com/kannan-prodapt/AIopsTelemetry) — observability server
- [WebSearchAgent](https://github.com/kannan-prodapt/WebSearchAgent) — LangGraph web search agent

---

## License

MIT
