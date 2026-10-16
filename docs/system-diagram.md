# VacHunter: current backend

This diagram shows the implemented Phase 1 backend. MongoDB runs locally in Docker Compose or can be configured to use Atlas; Atlas has not been validated. The API and worker are separate processes. No frontend is implemented.

```mermaid
flowchart TB
    CLI["CLI: initial / seed"]
    API["FastAPI<br/>POST /discovery · GET /vacancies<br/>GET /status · GET /health"]

    subgraph DB["MongoDB — local MongoDB 7 or configured Atlas"]
        Tasks[("tasks<br/>durable queue")]
        Companies[(companies)]
        Sources[(sources)]
        Vacancies[(vacancies)]
        Observations[(observations)]
        History[(history)]
    end

    Worker["Python async worker<br/>claims, renewable leases and retries"]
    Web["Shared HTTP client<br/>robots checks, TLS, pacing<br/>timeouts and Retry-After"]
    Discovery["QLever public Wikidata mirror<br/>paginated company discovery"]
    Careers["Company and career pages<br/>career links and ATS detection"]
    ATS["Official public ATS APIs<br/>Greenhouse · Lever · Ashby · Recruitee"]
    Classify["Extract and classify<br/>province · language · skills · seniority<br/>technical relevance and canonical URL"]

    CLI -->|enqueue by province and cycle| Tasks
    API -->|enqueue discovery| Tasks
    Tasks -->|claim due work| Worker
    Worker -->|renew lease and record task outcome| Tasks
    Worker --> Web
    Web --> Discovery
    Web --> Careers
    Web --> ATS
    Discovery -->|company identity and website| Companies
    Discovery -->|crawl tasks and next discovery page| Tasks
    Careers -->|career navigation and ATS collection tasks| Tasks
    Careers -->|JSON-LD JobPosting| Classify
    ATS -->|complete public board snapshot| Classify
    Classify -->|idempotent vacancy upserts| Vacancies
    Worker -->|source coverage and errors| Sources
    Worker -->|source presence and board reconciliation| Observations
    Worker -->|per-attempt results and errors| History
    Vacancies -->|filtered results| API
    Tasks -->|queue status| API
    Companies -->|counts| API
    Sources -->|counts| API
```

Province priority: **Limburg → Noord-Brabant → Gelderland → Utrecht → Noord-Holland**. Vacancy scope is an English technical role with a resolved location in one of these provinces. There is no driving-distance filter. Out-of-scope and unresolved collected vacancies are retained.

The worker creates follow-up tasks as discovery and crawling progress. Reusing a cycle resumes existing work; a new cycle refreshes it. A successful complete ATS snapshot can mark missing vacancies inactive; failed collections and partial HTML snapshots do not close unseen jobs. Request pacing is process-local, so the current recommended live topology uses one worker process with internal concurrency.

The API requires `X-API-Key` except for `/health`. It reads vacancy results and operational counts, and enqueues discovery; collection runs in the worker. GitHub Actions runs pytest with MongoDB 7 on pushes and pull requests. A recurring scheduler and a frontend are future work.

Discovery currently depends on businesses and headquarters represented in Wikidata. Location classification uses a starter city dictionary. Unsupported ATS products and JavaScript-only pages remain coverage gaps. See [architecture and implementation plan](architecture.md) and [validation report](validation.md) for details.
