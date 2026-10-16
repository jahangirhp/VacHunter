import json
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pymongo import MongoClient, ReturnDocument


def now():
    return datetime.now(timezone.utc)


def key(*parts):
    normalized = [json.dumps(part, sort_keys=True, separators=(',', ':')) if isinstance(part, (dict, list)) else str(part) for part in parts]
    return sha256('|'.join(normalized).encode()).hexdigest()


class Store:
    def __init__(self, db):
        self.db = db

    @classmethod
    def connect(cls, settings):
        return cls(MongoClient(settings.mongo_uri, tz_aware=True)[settings.mongo_database])

    def indexes(self):
        self.db.companies.create_index('domain', unique=True)
        self.db.sources.create_index([('company_id', 1), ('url', 1)], unique=True)
        self.db.tasks.create_index([('status', 1), ('available_at', 1), ('priority', 1)])
        self.db.vacancies.create_index([('company_id', 1), ('canonical_url', 1)], unique=True)
        self.db.vacancies.create_index([('province', 1), ('language', 1), ('relevant', 1), ('active', 1)])
        self.db.history.create_index([('task_id', 1), ('started_at', -1)])

    def enqueue(self, kind, payload, priority=0, cycle='initial'):
        ident = key(kind, payload, cycle)
        self.db.tasks.update_one({'_id': ident}, {'$setOnInsert': {
            'kind': kind, 'payload': payload, 'priority': priority, 'status': 'pending',
            'attempts': 0, 'available_at': now(), 'created_at': now()}}, upsert=True)
        return ident

    def claim(self, owner, lease_seconds):
        stamp = now()
        return self.db.tasks.find_one_and_update(
            {'$or': [{'status': 'pending', 'available_at': {'$lte': stamp}},
                     {'status': 'running', 'lease_until': {'$lt': stamp}}]},
            {'$set': {'status': 'running', 'owner': owner, 'lease_until': stamp + timedelta(seconds=lease_seconds)},
             '$inc': {'attempts': 1}}, sort=[('priority', 1), ('created_at', 1)], return_document=ReturnDocument.AFTER)

    def finish(self, task, owner, error=None, max_attempts=5, retry_delay=None):
        state = 'done' if error is None else ('failed' if task['attempts'] >= max_attempts else 'pending')
        self.db.tasks.update_one({'_id': task['_id'], 'owner': owner, 'status': 'running'}, {'$set': {
            'status': state, 'last_error': error, 'finished_at': now(),
            'available_at': now() + timedelta(seconds=retry_delay if retry_delay is not None else min(3600, 2 ** task['attempts']))}})
