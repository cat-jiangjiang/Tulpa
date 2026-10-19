"""Streaming SHA-256 for local files: one implementation shared by every caller."""
import hashlib
from pathlib import Path


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()
