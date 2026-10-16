from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVINCES = ['Limburg', 'Noord-Brabant', 'Gelderland', 'Utrecht', 'Noord-Holland']
PROVINCE_IDS = dict(zip(PROVINCES, ['Q1093', 'Q1101', 'Q775', 'Q776', 'Q701']))

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    mongo_uri: str = 'mongodb://localhost:27017'
    mongo_database: str = 'jobintel'
    discovery_endpoint: str = 'https://qlever.dev/api/wikidata'
    user_agent: str = 'JobIntelBot/0.1 (+https://example.org/jobintel; configure-contact-before-live-use)'
    concurrency: int = Field(default=4, ge=1, le=32)
    request_interval: float = Field(default=1.0, ge=0)
    max_attempts: int = Field(default=5, ge=1, le=20)
    lease_seconds: int = Field(default=900, ge=30)
    api_key: str = ''
