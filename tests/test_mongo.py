import os
import uuid
import pytest
from pymongo import MongoClient
from jobintel.store import Store

@pytest.mark.skipif(not os.getenv('TEST_MONGO_URI'), reason='Set TEST_MONGO_URI for real MongoDB integration')
def test_real_mongo_indexes_and_task_claim():
    client = MongoClient(os.environ['TEST_MONGO_URI'], tz_aware=True, serverSelectionTimeoutMS=5000)
    name = 'jobintel_test_' + uuid.uuid4().hex
    try:
        store = Store(client[name])
        store.indexes()
        store.enqueue('test', {'value': 1})
        claimed = store.claim('worker-1', 900)
        assert claimed['attempts'] == 1
        assert store.claim('worker-2', 900) is None
        store.finish(claimed, 'worker-1')
        assert store.db.tasks.count_documents({'status': 'done'}) == 1
    finally:
        client.drop_database(name)
        client.close()
