# RCA Knowledge Base Storage

The default local setup still works with SQLite. For a scaled RCA knowledge
base, run PostgreSQL with pgvector:

```bash
docker compose -f ../docker-compose.rca-kb.yml up -d
```

Use this database URL for AIopsTelemetry:

```env
AIOPS_DATABASE_URL=postgresql+psycopg://aiops:aiops@localhost:5432/aiops
```

`postgres_pgvector.sql` enables the `vector` extension. After SQLAlchemy creates
the RCA knowledge tables, the application adds vector columns and ivfflat
indexes at startup. The application also keeps JSON embedding fallback columns
so SQLite remains usable for local demos.

Initial MVP matching uses deterministic keyword and history matching. The
pgvector columns are ready for embeddings from issue summaries, RCA evidence,
playbooks, and validated historical fixes.
