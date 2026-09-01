# ARGUS Backend (P4 Day-1 Scaffold)

This folder contains the initial FastAPI scaffold and frozen Day-1 API contract for the ARGUS MVP backend role (P4).

## Run locally

1. Install dependencies:
   ```bash
   pip install -r /home/runner/work/ARGUS/ARGUS/backend/requirements.txt
   ```
2. Start API:
   ```bash
   uvicorn app.main:app --app-dir /home/runner/work/ARGUS/ARGUS/backend --reload
   ```
3. Open API docs:
   - Swagger UI: `http://127.0.0.1:8000/docs`
   - OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## Included Day-1 endpoints

- `POST /auth/login`
- `GET /search/entities`
- `GET /entities/{entity_id}`
- `POST /graph/expand`
- `GET /cases`
- `POST /cases`
- `GET /cases/{case_id}`
- `PATCH /cases/{case_id}`
- `POST /cases/{case_id}/entities`
- `POST /cases/{case_id}/notes`
- `GET /patterns`
- `GET /patterns/{pattern_id}`
- `POST /patterns/{pattern_id}/feedback`
- `POST /reports/export`
