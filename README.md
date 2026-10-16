# Dutch Job Intelligence — Phase 1

Working Python/FastAPI/MongoDB backend for automated company discovery and vacancy collection across Limburg, Noord-Brabant, Gelderland, Utrecht and Noord-Holland, prioritized in that order. No frontend or distance filter.

See [architecture, schema and implementation plan](docs/architecture.md) for supported contracts, coverage boundaries and production work still required. This initial code is production-oriented infrastructure, not an exhaustive or production-certified crawler.

## Small integration test first

Requires Python 3.11+:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

Tests run without credentials or network. The small integration simulates the Wikidata mirror → company careers link → official Lever API → province/language/skills/seniority classification → MongoDB-compatible storage. It runs twice to check deduplication. Adapter, robots, navigation and task ownership tests are included. Mongomock does not validate MongoDB server behavior; the optional real-server test is described below.

## Configuration

```sh
cp .env.example .env
```

Set USER_AGENT to a real bot information URL and contact address before live collection. Set API_KEY to a random secret. Never commit .env. For Atlas set MONGO_URI to your supplied mongodb+srv URI, use a least-privilege database user, TLS, and the Atlas network access list. No Atlas credentials are included or provisioned by this project.

For local Docker Compose, set `MONGO_URI=mongodb://mongo:27017` in .env. For Python processes outside Docker use `mongodb://localhost:27017`. Atlas uses the same URI from either environment.

## Run

```sh
docker compose up --build -d
# Queue all five provinces. The worker service is already watching.
docker compose exec worker jobintel seed --cycle initial-2026-10
# Inspect completion and failures:
docker compose exec worker jobintel status
```

For development without the app containers, start a local MongoDB or configure Atlas:

```sh
uvicorn jobintel.api:app --host 127.0.0.1 --port 8000
# In a separate shell:
jobintel initial --cycle initial-2026-10 --page-size 100
```

`initial` seeds all five provinces and drains the queue. `seed` only enqueues; `worker` drains existing work; `worker --watch` stays running. Restart with the **same** cycle to resume. Use a new cycle for refresh. Do not run two live worker processes against the same sites until distributed host throttling is added; CONCURRENCY controls safe process-local async collection.

The initial discovery has no province-specific company limit. Wikidata-mirror pages continue until exhausted. Work is persisted as it is discovered; a failure does not discard other sources. Check `/status` for failed tasks and unresolved locations. Do not interpret queue completion as exhaustive coverage.

## API

OpenAPI: http://localhost:8000/docs

- `GET /health`: database connectivity.
- `POST /discovery?cycle=unique-cycle`: enqueue an initial/refresh run, requires `X-API-Key`.
- `GET /status`: queue counts and failure details, requires `X-API-Key`.
- `GET /vacancies?province=Noord-Brabant&limit=50&offset=0`: active English technical vacancies by default, requires `X-API-Key`. Set `in_scope=false` to inspect all active collected jobs.

API startup creates indexes, but collection runs only in the worker. An external scheduler can enqueue a new dated cycle daily. Watch source success times and history errors before adding sources or increasing concurrency. Failed tasks stop after MAX_ATTEMPTS; diagnose their last_error and start a new cycle rather than deleting history.

## Optional real MongoDB integration

Start a disposable MongoDB server, then run:

```sh
TEST_MONGO_URI=mongodb://localhost:27017 pytest -q tests/test_mongo.py
```

This test creates and drops a uniquely named test database. Never point it at a database account that forbids temporary database creation.

## Known coverage gaps

Wikidata is a working automated seed provider, but omits many companies and branch offices. Four ATS adapters and static JSON-LD pages work today. JavaScript-only sites, other ATS products, unsupported city names and deep navigation need further work. Every collected vacancy is retained for audit, including non-English and unresolved records. See the implementation plan before treating results as a complete labor-market dataset.

## Discovery provider and validation

Discovery defaults to QLever’s public Wikidata mirror (`https://qlever.dev/api/wikidata`), whose robots policy permits this endpoint. The official query.wikidata.org SPARQL endpoint disallows crawling and was correctly rejected in a live check; the code does not override that policy. Configure DISCOVERY_ENDPOINT only for a compatible provider that permits your use. Wikidata-derived data should be attributed to Wikidata; company websites and job text retain their respective rights.

The container supports an optional BuildKit `proxy_ca` secret for environments using a TLS proxy. Standard environments need no secret. In this managed environment the image was built with the system CA bundle mounted as that secret. Runtime containers behind a TLS proxy also need the appropriate CA bundle mounted and SSL_CERT_FILE configured.

Recorded test results and live smoke-check boundaries: [validation report](docs/validation.md).
