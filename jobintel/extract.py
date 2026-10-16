import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup
from langdetect import detect, DetectorFactory, LangDetectException

DetectorFactory.seed = 0
CITIES = {
    'Limburg': ['maastricht', 'venlo', 'heerlen', 'sittard', 'roermond', 'weert', 'geleen'],
    'Noord-Brabant': ['eindhoven', 'tilburg', 'breda', 'den bosch', "s-hertogenbosch", 'helmond', 'best', 'veldhoven', 'oss'],
    'Gelderland': ['arnhem', 'nijmegen', 'apeldoorn', 'wageningen', 'ede', 'doetinchem', 'harderwijk'],
    'Utrecht': ['utrecht', 'amersfoort', 'zeist', 'nieuwegein', 'veenendaal', 'houten'],
    'Noord-Holland': ['amsterdam', 'haarlem', 'hilversum', 'alkmaar', 'hoofddorp', 'amstelveen', 'zaandam', 'schiphol'],
}
SKILLS = ['python', 'java', 'javascript', 'typescript', 'react', 'next.js', 'c++', 'c#', 'golang', 'rust', 'sql', 'mongodb', 'kubernetes', 'docker', 'aws', 'azure', 'pytorch', 'tensorflow', 'machine learning', 'llm', 'nlp']


def canonical(url):
    p = urlsplit(url)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid')))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', query, ''))


def classify(job):
    title = job['title'].lower()
    description = BeautifulSoup(job.get('description', ''), 'html.parser').get_text(' ', strip=True)
    text = title + ' ' + description.lower()
    location = str(job.get('location', '')).lower()
    provinces = [p for p, cities in CITIES.items() if p.lower() in location or any(re.search(r'(?<!\w)' + re.escape(c) + r'(?!\w)', location) for c in cities)]
    try:
        language = detect(description) if len(description) >= 80 else 'unknown'
    except LangDetectException:
        language = 'unknown'
    skills = [s for s in SKILLS if re.search(r'(?<!\w)' + re.escape(s) + r'(?!\w)', text)]
    seniority = next((label for pattern, label in [(r'\b(intern|internship|trainee)\b', 'intern'), (r'\b(junior|graduate|entry.level)\b', 'junior'), (r'\b(principal|staff)\b', 'staff'), (r'\b(lead|head|manager|director)\b', 'lead'), (r'\b(senior|sr)\b', 'senior')] if re.search(pattern, title)), 'unspecified')
    relevant = bool(re.search(r'\b(software|developer|programmer|machine learning|artificial intelligence|data scien(?:ce|tist)|ml engineer|ai engineer|devops|platform engineer|backend|frontend|full.?stack|data engineer|cloud engineer|site reliability|embedded|firmware|qa engineer|test automation)\b', title))
    return {**job, 'province': provinces, 'language': language, 'skills': skills, 'seniority': seniority,
            'relevant': relevant, 'in_scope': bool(provinces) and language == 'en' and relevant,
            'classification_version': 1, 'location_unresolved': not bool(provinces)}


def ats(url):
    p = urlsplit(url)
    bits = p.path.strip('/').split('/')
    host = (p.hostname or '').lower()
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io') and bits[0]:
        return 'greenhouse', f'https://boards-api.greenhouse.io/v1/boards/{bits[0]}/jobs?content=true'
    if host == 'jobs.lever.co' and bits[0]:
        return 'lever', f'https://api.lever.co/v0/postings/{bits[0]}?mode=json'
    if host == 'jobs.ashbyhq.com' and bits[0]:
        return 'ashby', f'https://api.ashbyhq.com/posting-api/job-board/{bits[0]}'
    if host.endswith('.recruitee.com'):
        return 'recruitee', f'https://{host}/api/offers/'
    return None


def page(html, base):
    soup = BeautifulSoup(html, 'html.parser')
    jobs = []
    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            types = value.get('@type', [])
            if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
                jobs.append({'external_id': str(value.get('identifier', '')), 'title': value.get('title', ''),
                             'description': value.get('description', ''), 'location': json.dumps(value.get('jobLocation', {})),
                             'url': urljoin(base, value.get('url') or base), 'valid_through': value.get('validThrough')})
            if '@graph' in value:
                walk(value['@graph'])
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    links = set()
    for a in soup.select('a[href], iframe[src]'):
        url = urljoin(base, a.get('href') or a.get('src'))
        if ats(url) or re.search(r'career|vacanc|jobs|werken|vacature|opening|position', url + ' ' + a.get_text(), re.I):
            if urlsplit(url).scheme in ('http', 'https'):
                links.add(url.split('#')[0])
    return jobs, sorted(links)


async def collect(web, kind, url):
    data = (await web.get(url)).json()
    if kind == 'greenhouse':
        return [{'external_id': str(j['id']), 'title': j['title'], 'url': j['absolute_url'], 'location': j.get('location', {}).get('name', ''), 'description': j.get('content', '')} for j in data['jobs']]
    if kind == 'lever':
        # Lever returns the complete public postings array when no skip/limit is supplied.
        return [{'external_id': j['id'], 'title': j['text'], 'url': j['hostedUrl'], 'location': ' '.join([j.get('categories', {}).get('location', ''), *j.get('categories', {}).get('allLocations', [])]), 'description': j.get('descriptionPlain', '') + ' ' + ' '.join(x.get('content', '') for x in j.get('lists', []))} for j in data]
    if kind == 'ashby':
        return [{'external_id': j['id'], 'title': j['title'], 'url': j['jobUrl'], 'location': j.get('location', '') + ' ' + json.dumps(j.get('secondaryLocations', [])), 'description': j.get('descriptionPlain', '')} for j in data['jobs'] if j.get('isListed', True)]
    if kind == 'recruitee':
        return [{'external_id': str(j['id']), 'title': j['title'], 'url': j['careers_url'], 'location': json.dumps(j.get('locations', j.get('location', ''))), 'description': j.get('description', '') + ' ' + j.get('requirements', '')} for j in data['offers']]
    raise ValueError(f'Unsupported ATS: {kind}')
