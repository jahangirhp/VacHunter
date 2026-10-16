import asyncio
import ipaddress
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import httpx


class Restricted(RuntimeError):
    pass


class Deferred(RuntimeError):
    def __init__(self, seconds):
        super().__init__(f'Retry after {seconds} seconds')
        self.seconds = seconds


class Web:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(headers={'User-Agent': settings.user_agent}, timeout=40,
                                        transport=transport, follow_redirects=False)
        self.testing = transport is not None
        self.locks = defaultdict(asyncio.Lock)
        self.last = defaultdict(float)
        self.robots = {}

    async def validate(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise Restricted('Unsupported URL')
        if not self.testing:
            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
            if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise Restricted('Non-public destination')

    async def request(self, url, headers=None):
        async with self.client.stream('GET', url, headers=headers) as response:
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 10_000_000:
                    raise Restricted('Response exceeds 10 MB')
            # aiter_bytes decodes compression; do not decode a second time.
            clean_headers = {k: v for k, v in response.headers.items() if k.lower() not in ('content-encoding', 'content-length')}
            return httpx.Response(response.status_code, headers=clean_headers, content=bytes(content), request=response.request)

    async def raw(self, url, headers=None):
        for redirect in range(6):
            await self.validate(url)
            host = urlsplit(url).netloc
            async with self.locks[host]:
                origin = f'{urlsplit(url).scheme}://{host}'
                robot = self.robots.get(origin)
                crawl_delay = (robot.crawl_delay(self.settings.user_agent) or robot.crawl_delay('*') or 0) if robot else 0
                await asyncio.sleep(max(0, max(self.settings.request_interval, crawl_delay) - (time.monotonic() - self.last[host])))
                response = await self.request(url, headers=headers)
                self.last[host] = time.monotonic()
            if response.is_redirect:
                target = urljoin(url, response.headers['location'])
                # Every redirect target must also pass robots policy.
                await self.allowed(target)
                url = target
                headers = None
                continue
            response.raise_for_status()
            if len(response.content) > 10_000_000:
                raise Restricted('Response exceeds 10 MB')
            return response
        raise Restricted('Redirect limit')

    async def allowed(self, url):
        parsed = urlsplit(url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        if origin not in self.robots:
            # Robots redirects are conservatively refused to avoid recursive policy checks.
            await self.validate(origin + '/robots.txt')
            async with self.locks[parsed.netloc]:
                await asyncio.sleep(max(0, self.settings.request_interval - (time.monotonic() - self.last[parsed.netloc])))
                response = await self.request(origin + '/robots.txt')
                self.last[parsed.netloc] = time.monotonic()
            if response.status_code == 404:
                body = ''
            elif response.status_code == 200:
                body = response.text
            else:
                raise Restricted(f'Robots policy unavailable: {response.status_code}')
            robot = RobotFileParser()
            robot.parse(body.splitlines())
            self.robots[origin] = robot
        robot = self.robots[origin]
        if not robot.can_fetch(self.settings.user_agent, url):
            raise Restricted('Disallowed by robots.txt')

    async def get(self, url, headers=None):
        for attempt in range(4):
            try:
                await self.allowed(url)
                return await self.raw(url, headers)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                value = exc.response.headers.get('Retry-After', '')
                try:
                    delay = float(value)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError):
                        delay = 2 ** attempt
                if delay > 60:
                    raise Deferred(delay) from exc
                await asyncio.sleep(max(0, delay))
            except httpx.TransportError:
                if attempt == 3:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError('Retry exhausted')
