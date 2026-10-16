# Validation — 2026-10-16

- `TEST_MONGO_URI=mongodb://localhost:27017 python -m pytest -q`: **18 passed**. One upstream Starlette TestClient deprecation warning; no failures.
- Tests cover mocked discovery → official ATS contract → classification → storage and repeat-run deduplication, against both mongomock and real MongoDB 7. External HTTP is mocked for repeatability.
- Additional checks cover each supported adapter, JSON-LD graphs, HTML pagination links, persisted discovery pagination, robots refusal, redirect policy, long Retry-After deferral, atomic task claims, lease recovery, owner-fenced completion, payload-order-independent task identity, API authentication and failed/empty board snapshot behavior.
- Live public QLever Wikidata API: successful SPARQL response. A one-company discovery query succeeded separately for **all five provinces**. Temporary smoke databases were dropped. This was not a full live collection or exhaustive discovery run.
- Official query.wikidata.org SPARQL requests were correctly refused due to its robots policy. QLever is an independent permitted public mirror; the crawler does not override denied access.
- Docker image `jobintel:phase1` built successfully. Container ran as non-root with MongoDB 7 on a private Docker network. `/health` returned 200, unauthenticated `/status` returned 401, authenticated `/status` returned 200.
- Atlas was not tested because no Atlas credentials were configured. Live ATS extraction and large-scale source coverage have not been certified. See architecture.md for remaining production and coverage work.

Disposable test containers and network were removed after validation; the built application image remains available locally.
