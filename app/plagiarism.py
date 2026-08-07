import hashlib
import os
import re
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}

SOURCE_EXTENSIONS = (
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".php",
    ".dart",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".cs",
)


def _normalize_source(text: str) -> str:
    """
    Remove unnecessary whitespace before hashing.
    """
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compute_source_hash(repo_path: str) -> str:
    """
    Computes a deterministic hash of repository source code.
    Ignores build folders and binary files.
    """
    hasher = hashlib.sha256()

    for root, dirs, files in os.walk(repo_path):

        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for filename in sorted(files):

            if not filename.endswith(SOURCE_EXTENSIONS):
                continue

            path = os.path.join(root, filename)

            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = _normalize_source(f.read())

                relative = os.path.relpath(path, repo_path)

                hasher.update(relative.encode())
                hasher.update(content.encode())

            except Exception as e:
                logger.debug("Failed hashing %s : %s", path, e)

    return hasher.hexdigest()


def check_similarity(new_hash: str, existing_hashes: list[str]) -> float:
    """
    Returns the highest similarity percentage.
    Exact hash match = 100%.
    Otherwise compares hashes using SequenceMatcher.
    """

    if not existing_hashes:
        return 0.0

    best = 0.0

    for old_hash in existing_hashes:

        if new_hash == old_hash:
            return 100.0

        similarity = SequenceMatcher(None, new_hash, old_hash).ratio() * 100

        if similarity > best:
            best = similarity

    return round(best, 2)