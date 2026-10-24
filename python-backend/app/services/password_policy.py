"""Common-password screening for registration, backed by a Bloom filter (app.domain.bloom)."""

import functools
from pathlib import Path

import structlog

from app.config.settings import get_settings
from app.domain.bloom import BloomFilter

logger = structlog.get_logger()

BUNDLED_LIST = Path(__file__).resolve().parent.parent / "resources" / "common_passwords.txt"
FALSE_POSITIVE_RATE = 0.001


def normalize(password: str) -> str:
    """Case-insensitive: "Password1234" is as guessable as "password1234"."""
    return password.strip().lower()


def load_entries(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [normalize(line) for line in lines if line.strip() and not line.startswith("#")]


@functools.cache
def common_passwords() -> BloomFilter:
    """Built once per process from COMMON_PASSWORDS_PATH, or the bundled list when unset."""
    configured = get_settings().common_passwords_path
    path = Path(configured) if configured else BUNDLED_LIST
    entries = load_entries(path)
    bloom = BloomFilter.of(entries, FALSE_POSITIVE_RATE)
    logger.info("common_password_filter_loaded", entries=len(entries), bits=bloom.bit_count,
                hashes=bloom.hash_count, bundled=not configured)
    return bloom


def is_common(password: str) -> bool:
    return normalize(password) in common_passwords()
