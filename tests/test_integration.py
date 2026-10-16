import json
import os
import uuid
from pymongo import MongoClient
import httpx
import mongomock
import pytest
from jobintel.config import Settings, PROVINCES
from jobintel.http import Web, Restricted
from jobintel.pipeline import Pipeline
from jobintel.store import Store
from jobintel.extract import classify, page

DESCRIPTION = 'We are looking for a software engineer to build our cloud platform. You will work with our team to develop reliable software using Python and Docker. We offer a flexible working environment and opportunities to learn.'

@pytest.fixture(params=['mock', 'mongo'])
def integration_store(request):
    if request.param == 'mongo' and not os.getenv('TEST_MONGO_URI'):
        pytest.skip('Set TEST_MONGO_URI for real end-to-end persistence')
    client = MongoClient(os.environ['TEST_MONGO_URI'], tz_aware=True) if request.param == 'mongo' else mongomock.MongoClient(tz_aware=True)
    name = 'jobintel_test_' + uuid.uuid4().hex
    try:
        yield Store(client[name])
    finally:
        client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_discovery_to_vacancy_and_repeat(integration_store):
    hits = []
    def serve(request):
        hits.append(str(request.url))
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if request.url.host == 'qlever.dev':
            return httpx.Response(200, json={'results': {'bindings': [{'company': {'value': 'http://www.wikidata.org/entity/Q123'}, 'companyLabel': {'value': 'Example Tech'}, 'website': {'value': 'https://example.test'}}]}})
        if request.url.host == 'example.test':
            return httpx.Response(200, text='<a href="https://jobs.lever.co/example">Careers</a>')
        if request.url.host == 'api.lever.co':
            return httpx.Response(200, json=[{'id': '42', 'text': 'Senior Software Engineer', 'hostedUrl': 'https://jobs.lever.co/example/42', 'categories': {'location': 'Eindhoven, Netherlands'}, 'descriptionPlain': DESCRIPTION}])
        return httpx.Response(404)
    settings = Settings(request_interval=0, concurrency=2)
    store = integration_store
    store.indexes()
    web = Web(settings, httpx.MockTransport(serve))
    pipeline = Pipeline(store, web, settings)
    try:
        pipeline.seed()
        await pipeline.run()
        assert store.db.tasks.count_documents({'status': 'failed'}) == 0
        assert store.db.companies.count_documents({}) == 1
        assert store.db.vacancies.count_documents({}) == 1
        job = store.db.vacancies.find_one()
        assert job['province'] == ['Noord-Brabant']
        assert job['in_scope'] and job['language'] == 'en'
        assert job['seniority'] == 'senior'
        assert {'python', 'docker'} <= set(job['skills'])
        assert set(store.db.companies.find_one()['discovery_provinces']) == set(PROVINCES)
        pipeline.seed(cycle='second')
        await pipeline.run()
        assert store.db.vacancies.count_documents({}) == 1
        assert store.db.history.count_documents({'error': {'$ne': None}}) == 0
    finally:
        await web.client.aclose()

@pytest.mark.asyncio
async def test_robots_denial():
    web = Web(Settings(request_interval=0), httpx.MockTransport(lambda r: httpx.Response(200, text='User-agent: *\nDisallow: /')))
    try:
        with pytest.raises(Restricted):
            await web.get('https://example.test/private')
    finally:
        await web.client.aclose()


def test_unknown_location_and_short_language_are_not_in_scope():
    result = classify({'title': 'Software Engineer', 'description': 'Python', 'location': 'Remote'})
    assert not result['in_scope'] and result['language'] == 'unknown'


def test_graph_jobposting_and_pagination():
    jobs, links = page('<script type="application/ld+json">' + json.dumps({'@graph': [{'@type': 'JobPosting', 'title': 'Engineer', 'url': '/jobs/1'}]}) + '</script><a href="/jobs?page=2">Next</a>', 'https://example.test')
    assert jobs[0]['url'] == 'https://example.test/jobs/1'
    assert links == ['https://example.test/jobs?page=2']


def test_atomic_claim_and_owner_fencing():
    store = Store(mongomock.MongoClient(tz_aware=True).db)
    ident = store.enqueue('test', {})
    task = store.claim('one', 900)
    assert store.claim('two', 900) is None
    store.finish(task, 'two')
    assert store.db.tasks.find_one({'_id': ident})['status'] == 'running'
    store.finish(task, 'one')
    assert store.db.tasks.find_one({'_id': ident})['status'] == 'done'

@pytest.mark.asyncio
async def test_discovery_pagination_persists_next_page():
    offsets = []
    def serve(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        query = request.url.params['query']
        offsets.append(query)
        rows = [] if 'OFFSET 1' in query else [{'company': {'value': 'Q1'}, 'website': {'value': 'https://example.test'}}]
        return httpx.Response(200, json={'results': {'bindings': rows}})
    settings = Settings(request_interval=0)
    store = Store(mongomock.MongoClient(tz_aware=True).db)
    web = Web(settings, httpx.MockTransport(serve))
    pipeline = Pipeline(store, web, settings)
    task = {'kind': 'discover', 'priority': 0, 'payload': {'province': 'Limburg', 'offset': 0, 'page_size': 1, 'cycle': 'page-test'}}
    try:
        await pipeline.execute(task)
        followup = store.db.tasks.find_one({'kind': 'discover'})
        assert followup['payload']['offset'] == 1
        result = await pipeline.execute(followup)
        assert result['companies_observed'] == 0
        assert len(offsets) == 2
    finally:
        await web.client.aclose()


def test_lease_recovery_and_stale_owner():
    from datetime import timedelta
    from jobintel.store import now
    store = Store(mongomock.MongoClient(tz_aware=True).db)
    ident = store.enqueue('test', {})
    first = store.claim('first', 900)
    store.db.tasks.update_one({'_id': ident}, {'$set': {'lease_until': now() - timedelta(seconds=1)}})
    second = store.claim('second', 900)
    assert second['attempts'] == 2
    store.finish(first, 'first')
    assert store.db.tasks.find_one({'_id': ident})['status'] == 'running'
    store.finish(second, 'second')
    assert store.db.tasks.find_one({'_id': ident})['status'] == 'done'

async def test_failed_board_does_not_close_jobs_but_empty_snapshot_does():
    state = {'body': [{'id': '42', 'text': 'Software Engineer', 'hostedUrl': 'https://jobs.lever.co/example/42', 'categories': {'location': 'Utrecht'}, 'descriptionPlain': DESCRIPTION}]}
    def serve(r):
        if r.url.path == '/robots.txt':
            return httpx.Response(404)
        return httpx.Response(200, json=state['body'])
    store = Store(mongomock.MongoClient(tz_aware=True).db)
    settings = Settings(request_interval=0)
    web = Web(settings, httpx.MockTransport(serve))
    pipeline = Pipeline(store, web, settings)
    task = {'kind': 'collect', 'priority': 0, 'payload': {'company_id': 'test', 'url': 'https://api.lever.co/v0/postings/example?mode=json', 'ats': 'lever', 'cycle': 'a'}}
    try:
        await pipeline.execute(task)
        assert store.db.vacancies.find_one()['active']
        state['body'] = {'unexpected_schema': []}
        with pytest.raises((TypeError, KeyError)):
            await pipeline.execute(task)
        assert store.db.vacancies.find_one()['active']
        state['body'] = []
        await pipeline.execute(task)
        assert not store.db.vacancies.find_one()['active']
    finally:
        await web.client.aclose()


def test_task_identity_does_not_depend_on_payload_order():
    store = Store(mongomock.MongoClient(tz_aware=True).db)
    first = store.enqueue('discover', {'province': 'Limburg', 'offset': 0})
    second = store.enqueue('discover', {'offset': 0, 'province': 'Limburg'})
    assert first == second
    assert store.db.tasks.count_documents({}) == 1
