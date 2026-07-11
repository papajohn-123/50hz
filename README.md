# 50Hz

Britain's electricity system, alive.

This repository will contain the SwiftUI application and its FastAPI data platform. The initial backend is deployable on Railway and exposes health and configuration-status endpoints.

## Local backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Copy `.env.example` to `.env` for local configuration. Never commit API keys.

## Railway configuration

The API expects:

- `DATABASE_URL`: a reference to the Railway PostgreSQL service
- `OPENROUTER_API_KEY`: added manually as a Railway secret
- `OPENROUTER_MODEL`: the selected OpenRouter model identifier
- `APP_ENV=production`

OpenRouter calls will be made only by the backend. The key must never be included in the iOS application.
