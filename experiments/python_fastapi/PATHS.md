# Python FastAPI Template Path Map

Pinned upstream repository:

- Repo: `https://github.com/tiangolo/full-stack-fastapi-template.git`
- Commit: `13652b51ea0acca7dfe243ac25e2bbdc066f3c4f`

Verified on 2026-05-06 by running `bash experiments/python_fastapi/setup.sh`.

Canonical repo-relative paths at this SHA:

- Route aggregation file: `backend/app/api/main.py`
- Settings file: `backend/app/core/config.py`
- App entrypoint: `backend/app/main.py`
- API routes directory: `backend/app/api/routes/`
- Backend tests directory: `backend/tests/`
- Route tests directory: `backend/tests/api/routes/`

Notes:

- There is no `backend/app/tests/` tree at this SHA. Route tests live under
  `backend/tests/api/routes/`.
- The route aggregation module defines `api_router` and currently includes
  routers from `login`, `users`, `utils`, `items`, and `private`.
- The app entrypoint imports `api_router` from `backend/app/api/main.py` and
  includes it with `prefix=settings.API_V1_STR`.
