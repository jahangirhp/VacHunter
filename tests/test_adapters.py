import httpx
import pytest
from jobintel.extract import canonical, collect
from jobintel.http import Web
from jobintel.config import Settings

@pytest.mark.parametrize(('kind', 'payload'), [
    ('greenhouse', {'jobs': [{'id': 1, 'title': 'Engineer', 'absolute_url': 'https://company.test/job/1', 'location': {'name': 'Utrecht'}, 'content': 'Python'}]}),
    ('ashby', {'jobs': [{'id': '1', 'title': 'Engineer', 'jobUrl': 'https://company.test/job/1', 'location': 'Utrecht', 'descriptionPlain': 'Python'}]}),
    ('recruitee', {'offers': [{'id': 1, 'title': 'Engineer', 'careers_url': 'https://company.test/job/1', 'location': 'Utrecht', 'description': 'Python'}]}),
])
async def test_adapter_contract(kind, payload):
    web = Web(Settings(request_interval=0), httpx.MockTransport(lambda r: httpx.Response(404) if r.url.path == '/robots.txt' else httpx.Response(200, json=payload)))
    try:
        jobs = await collect(web, kind, 'https://company.test/api')
        assert len(jobs) == 1 and jobs[0]['external_id'] == '1'
        assert jobs[0]['url'] == 'https://company.test/job/1'
    finally:
        await web.client.aclose()


def test_query_identity_is_preserved():
    assert canonical('https://company.test/job?id=1&utm_source=test') == 'https://company.test/job?id=1'
    assert canonical('https://company.test/job?id=1') != canonical('https://company.test/job?id=2')


async def test_redirect_checks_destination_robots():
    def serve(r):
        if r.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nDisallow: /' if r.url.host == 'blocked.test' else '')
        return httpx.Response(302, headers={'Location': 'https://blocked.test/job'})
    from jobintel.http import Restricted
    web = Web(Settings(request_interval=0), httpx.MockTransport(serve))
    try:
        with pytest.raises(Restricted):
            await web.get('https://company.test')
    finally:
        await web.client.aclose()

async def test_long_retry_after_is_deferred():
    from jobintel.http import Deferred
    def serve(r):
        if r.url.path == '/robots.txt':
            return httpx.Response(404)
        return httpx.Response(429, headers={'Retry-After': '120'})
    web = Web(Settings(request_interval=0), httpx.MockTransport(serve))
    try:
        with pytest.raises(Deferred) as exc:
            await web.get('https://company.test/api')
        assert exc.value.seconds == 120
    finally:
        await web.client.aclose()
