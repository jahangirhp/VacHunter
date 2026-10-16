import mongomock
from fastapi.testclient import TestClient
from jobintel.api import app, settings
from jobintel.store import Store


def test_authentication_seed_and_scope(monkeypatch):
    store = Store(mongomock.MongoClient(tz_aware=True).db)
    monkeypatch.setattr(Store, 'connect', lambda config: store)
    monkeypatch.setattr(settings, 'api_key', 'test-only-key')
    with TestClient(app) as client:
        assert client.get('/status').status_code == 401
        headers = {'X-API-Key': 'test-only-key'}
        assert client.post('/discovery?cycle=api-test', headers=headers).status_code == 202
        assert client.get('/status', headers=headers).json()['tasks']['pending'] == 5
        assert client.get('/vacancies?province=Unknown', headers=headers).status_code == 422
        assert client.get('/vacancies', headers=headers).json()['items'] == []
