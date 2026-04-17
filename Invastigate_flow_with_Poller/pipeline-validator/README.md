# Investigation Pipeline Validator

A standalone frontend UI to validate all 5 steps of the investigation agent flow:

**Normalization → Correlation → Analysis → RCA → Recommendations**

## Files

```
pipeline-validator/
├── index.html    — Main HTML structure
├── styles.css    — Styles (light + dark mode)
├── app.js        — Application logic
└── README.md     — This file
```

## Usage

### Option 1 — Open directly in browser
Just open `index.html` in any modern browser. No build step required.

### Option 2 — Serve with a local server (recommended)
```bash
# Python
python -m http.server 3000

# Node
npx serve .
```
Then open http://localhost:3000

## Connecting to your FastAPI backend

1. Start your server:
   ```bash
   cd Invastigate_flow_with_Poller
   uvicorn app.main:app --reload
   ```

2. In the UI, set **API base URL** to `http://localhost:8000`

3. Click **Check /api/v1/poller/status** to confirm connection

The UI will automatically call your real endpoints. If the server is unreachable, it falls back to mock data generated from your `output_format.json` samples.

## API endpoints used

| Step | Endpoint |
|------|----------|
| Normalization | `POST /api/v1/normalize` |
| Correlation | `POST /api/v1/correlate` |
| Analysis | `POST /api/v1/error-analysis` |
| RCA | `POST /api/v1/rca` |
| Recommendations | `POST /api/v1/recommend` |
| Poller health | `GET /api/v1/poller/status` |
| Full pipeline | `POST /api/v1/analyze` |
| Direct orchestrator | `POST /api/v1/investigate` |

## Features

- **4 quick scenarios** — pre-loaded with real trace IDs from your output_format.json
- **Full pipeline run** — executes all 5 steps in sequence, chaining outputs as inputs
- **Step-by-step mode** — advance one agent at a time
- **NO_ERROR short-circuit** — detects when normalization returns NO_ERROR and skips remaining steps (mirrors your orchestrator logic)
- **Validation checks** — per-step checks against your Pydantic models
- **Input/Output/Raw JSON tabs** — inspect request payloads and structured responses
- **Poller status panel** — check background poller health
- **Light + dark mode** — automatic based on system preference
- **Responsive** — works on mobile
