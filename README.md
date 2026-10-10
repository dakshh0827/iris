# Reg IRIS: Intelligent Regulatory Repository & Impact Scanner

Team project. Each service lives in its own folder under `services/`.

| Folder | What it is |
|---|---|
| `services/ingestion/` | Uploads, extracts, chunks, embeds and indexes regulatory documents (PDF, HTML, TXT). |
| `data/` | The supplied synthetic dataset: regulations, metadata, rules, schema, trades, queries. |
| `scripts/` | Developer checks for the local setup. |

## Ingestion service: run locally

Requires Python 3.11.

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1     macOS/Linux: source .venv/bin/activate
python -m pip install -r services/ingestion/requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
python scripts/check_setup.py

cd services/ingestion
pytest
uvicorn app.main:app --reload --port 8000
```

Then open http://127.0.0.1:8000/docs.