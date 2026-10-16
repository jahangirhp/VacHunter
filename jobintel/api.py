from contextlib import asynccontextmanager
from secrets import compare_digest
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from .config import Settings, PROVINCES
from .store import Store
from .pipeline import Pipeline

settings = Settings()

@asynccontextmanager
async def lifespan(app):
    app.state.store = Store.connect(settings)
    app.state.store.indexes()
    yield
    app.state.store.db.client.close()

app = FastAPI(title='Dutch Job Intelligence', version='0.1.0', lifespan=lifespan)


def authorized(x_api_key: str = Header(default='')):
    if not settings.api_key or not compare_digest(x_api_key, settings.api_key):
        raise HTTPException(401, 'Configure API_KEY and provide X-API-Key')


def public(doc):
    return {**doc, '_id': str(doc['_id'])}


@app.get('/health')
def health():
    app.state.store.db.command('ping')
    return {'status': 'ok'}


@app.post('/discovery', dependencies=[Depends(authorized)], status_code=202)
def discovery(cycle: str = Query(min_length=1, max_length=100)):
    Pipeline(app.state.store, None, settings).seed(cycle)
    return {'cycle': cycle, 'provinces': PROVINCES}


@app.get('/vacancies', dependencies=[Depends(authorized)])
def vacancies(province: str | None = None, in_scope: bool = True, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100000)):
    query = {'active': True}
    if in_scope:
        query['in_scope'] = True
    if province:
        if province not in PROVINCES:
            raise HTTPException(422, 'Unsupported province')
        query['province'] = province
    cursor = app.state.store.db.vacancies.find(query).sort([('last_seen_at', -1), ('_id', 1)]).skip(offset).limit(limit)
    return {'items': [public(x) for x in cursor], 'total': app.state.store.db.vacancies.count_documents(query)}


@app.get('/status', dependencies=[Depends(authorized)])
def status():
    db = app.state.store.db
    return {'tasks': {s: db.tasks.count_documents({'status': s}) for s in ['pending', 'running', 'done', 'failed']},
            'companies': db.companies.count_documents({}), 'sources': db.sources.count_documents({}),
            'vacancies': db.vacancies.count_documents({}),
            'unresolved_locations': db.vacancies.count_documents({'location_unresolved': True}),
            'failed_tasks': [public(x) for x in db.tasks.find({'status': 'failed'}).limit(20)]}
