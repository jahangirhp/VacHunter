import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlsplit
from .config import PROVINCES, PROVINCE_IDS
from .extract import ats, canonical, classify, collect, page
from .store import key, now
from .http import Deferred, Restricted

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store, web, settings):
        self.store, self.web, self.settings = store, web, settings

    def seed(self, cycle='initial', page_size=100):
        if not 1 <= page_size <= 500:
            raise ValueError('page_size must be 1..500')
        for priority, province in enumerate(PROVINCES):
            self.store.enqueue('discover', {'province': province, 'offset': 0, 'page_size': page_size, 'cycle': cycle}, priority, cycle)

    async def execute(self, task):
        payload = task['payload']
        cycle = payload['cycle']
        priority = task['priority']
        if task['kind'] == 'discover':
            province, offset, size = payload['province'], payload['offset'], payload['page_size']
            # Geography first: technology relevance is established from actual vacancies,
            # avoiding a narrow industry taxonomy that would miss engineering employers.
            query = f'''PREFIX wd: <http://www.wikidata.org/entity/>
            PREFIX wdt: <http://www.wikidata.org/prop/direct/>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT DISTINCT ?company ?companyLabel ?website WHERE {{
              ?company wdt:P31/wdt:P279* wd:Q4830453; wdt:P856 ?website; wdt:P159 ?hq.
              ?hq wdt:P131* wd:{PROVINCE_IDS[province]}.
              OPTIONAL {{ ?company rdfs:label ?companyLabel. FILTER(LANG(?companyLabel) = "en") }}
            }} ORDER BY ?company ?website LIMIT {size} OFFSET {offset}'''
            url = self.settings.discovery_endpoint + '?' + urlencode({'query': query, 'format': 'json'})
            rows = (await self.web.get(url, headers={'Accept': 'application/sparql-results+json'})).json()['results']['bindings']
            for row in rows:
                website = canonical(row['website']['value'])
                domain = (urlsplit(website).hostname or '').removeprefix('www.')
                if not domain:
                    continue
                ident = key(domain)
                self.store.db.companies.update_one({'_id': ident}, {'$set': {'domain': domain, 'name': row.get('companyLabel', {'value': domain})['value'], 'website': website, 'updated_at': now()}, '$addToSet': {'discovery_provinces': province, 'wikidata_ids': row['company']['value']}, '$setOnInsert': {'created_at': now()}}, upsert=True)
                self.store.enqueue('crawl', {'company_id': ident, 'url': website, 'depth': 0, 'cycle': cycle}, priority, cycle)
            if len(rows) == size:
                self.store.enqueue('discover', {**payload, 'offset': offset + size}, priority, cycle)
            return {'companies_observed': len(rows), 'province': province, 'offset': offset}
        company_id, url = payload['company_id'], payload['url']
        source_id = key(company_id, url)
        self.store.db.sources.update_one({'_id': source_id}, {'$set': {'company_id': company_id, 'url': url, 'kind': payload.get('ats', 'html'), 'last_attempt_at': now()}, '$setOnInsert': {'created_at': now()}}, upsert=True)
        if task['kind'] == 'collect':
            jobs = await collect(self.web, payload['ats'], url)
        else:
            response = await self.web.get(url)
            jobs, links = page(response.text, str(response.url))
            for link in links:
                detected = ats(link)
                if detected:
                    kind, endpoint = detected
                    self.store.enqueue('collect', {'company_id': company_id, 'url': endpoint, 'ats': kind, 'cycle': cycle}, priority, cycle)
                elif payload['depth'] < 4:
                    # Identity excludes depth, so navigation loops do not multiply work.
                    next_payload = {'company_id': company_id, 'url': canonical(link) if '?' not in link else link, 'depth': payload['depth'] + 1, 'cycle': cycle}
                    ident = key('crawl', company_id, next_payload['url'], cycle)
                    self.store.db.tasks.update_one({'_id': ident}, {'$setOnInsert': {'kind': 'crawl', 'payload': next_payload, 'priority': priority, 'status': 'pending', 'attempts': 0, 'available_at': now(), 'created_at': now()}}, upsert=True)
        stamp = now()
        for raw in jobs:
            if not raw.get('title') or not raw.get('url'):
                raise ValueError('Source returned a job without title or URL')
            job = classify(raw)
            normalized = canonical(job['url'])
            job['duplicate_group'] = key(company_id, job['title'].strip().lower(), str(job.get('location', '')).strip().lower())
            expired = False
            if job.get('valid_through'):
                try:
                    expires = datetime.fromisoformat(job['valid_through'].replace('Z', '+00:00'))
                    expired = expires.replace(tzinfo=expires.tzinfo or timezone.utc) < stamp
                except ValueError:
                    pass
            self.store.db.vacancies.update_one({'company_id': company_id, 'canonical_url': normalized}, {'$set': {**job, 'company_id': company_id, 'canonical_url': normalized, 'last_seen_at': stamp, 'active': not expired}, '$addToSet': {'source_ids': source_id}, '$setOnInsert': {'_id': key(company_id, normalized), 'first_seen_at': stamp}}, upsert=True)
            self.store.db.observations.update_one({'_id': key(source_id, normalized)}, {'$set': {'source_id': source_id, 'vacancy_id': key(company_id, normalized), 'seen_at': stamp, 'present': True}}, upsert=True)
        if task['kind'] == 'collect':
            # Only complete authoritative board snapshots can mark jobs missing.
            self.store.db.observations.update_many({'source_id': source_id, 'seen_at': {'$lt': stamp}}, {'$set': {'present': False}})
            for observation in self.store.db.observations.find({'source_id': source_id, 'present': False}):
                if not self.store.db.observations.find_one({'vacancy_id': observation['vacancy_id'], 'present': True}):
                    self.store.db.vacancies.update_one({'_id': observation['vacancy_id']}, {'$set': {'active': False}})
        self.store.db.sources.update_one({'_id': source_id}, {'$set': {'last_success_at': stamp, 'job_count': len(jobs), 'last_error': None, 'coverage_status': 'complete_board' if task['kind'] == 'collect' else 'partial_html'}})
        return {'jobs_observed': len(jobs), 'source_id': source_id}

    async def worker(self, drain=True):
        owner = str(uuid.uuid4())
        while True:
            task = await asyncio.to_thread(self.store.claim, owner, self.settings.lease_seconds)
            if not task:
                if drain:
                    outstanding = self.store.db.tasks.count_documents({'status': {'$in': ['pending', 'running']}})
                    if not outstanding:
                        return
                await asyncio.sleep(1)
                continue
            started = now()
            heartbeat = asyncio.create_task(self.heartbeat(task['_id'], owner))
            result, error, retry_delay, terminal = None, None, None, False
            try:
                result = await self.execute(task)
            except Exception as exc:
                error = f'{type(exc).__name__}: {str(exc)[:1000]}'
                retry_delay = exc.seconds if isinstance(exc, Deferred) else None
                terminal = isinstance(exc, Restricted)
                if task['kind'] != 'discover':
                    self.store.db.sources.update_one({'_id': key(task['payload']['company_id'], task['payload']['url'])}, {'$set': {'last_error': error, 'coverage_status': 'blocked' if terminal else 'error'}})
                log.warning('Task %s failed: %s', task['_id'], error)
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            self.store.db.history.insert_one({'task_id': task['_id'], 'attempt': task['attempts'], 'started_at': started, 'finished_at': now(), 'result': result, 'error': error})
            await asyncio.to_thread(self.store.finish, task, owner, error, 1 if terminal else self.settings.max_attempts, retry_delay)

    async def heartbeat(self, ident, owner):
        while True:
            await asyncio.sleep(self.settings.lease_seconds / 3)
            await asyncio.to_thread(self.store.db.tasks.update_one, {'_id': ident, 'owner': owner, 'status': 'running'}, {'$set': {'lease_until': now() + timedelta(seconds=self.settings.lease_seconds)}})

    async def run(self):
        await asyncio.gather(*(self.worker() for _ in range(self.settings.concurrency)))
