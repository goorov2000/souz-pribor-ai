from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env.local', override=False)


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv('OPENAI_API_KEY', ''), repr=False)
    model: str = field(default_factory=lambda: os.getenv('OPENAI_MODEL', 'gpt-6-luna'))
    timeout: float = field(default_factory=lambda: float(os.getenv('OPENAI_TIMEOUT_SECONDS', '45')))
