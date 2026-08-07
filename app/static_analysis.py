import os
import re
import json
import logging

logger = logging.getLogger(__name__)

# Constants
MAX_FILE_SIZE = 1024 * 1024  # 1 MB threshold for safe reading
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "dist",
    "build",
}

# Regex patterns for scanning sensitive tokens and exposed secrets
SECRET_PATTERNS = [
    r'(api[_-]?key)\s*=\s*["\'][A-Za-z0-9]{16,}["\']',
    r'(secret|password|passwd)\s*=\s*["\'][^"\']{6,}["\']',
    r'AKIA[0-9A-Z]{16}',
    r'ghp_[A-Za-z0-9]{36}',                 # GitHub Personal Access Token
    r'AIza[0-9A-Za-z\-_]{35}',             # Google API Key
    r'sk-[A-Za-z0-9]{32,}',                 # OpenAI / Generic Secret Key
    r'xox[baprs]-[A-Za-z0-9-]+',            # Slack Token
    r'-----BEGIN PRIVATE KEY-----',          # RSA / Private Keys
]


def check_project_structure(repo_path: str, stack: str) -> dict:
    """Analyzes overall repository file tree, key configurations, and architectural layout."""
    try:
        files = set(os.listdir(repo_path))
    except Exception as e:
        logger.error("Failed to list files in repository path %s: %s", repo_path, e)
        files = set()

    checks = {}

    checks["Project Structure"] = len(files) > 2
    checks["Folder Organization"] = any(
        os.path.isdir(os.path.join(repo_path, f)) for f in files if f not in IGNORE_DIRS
    )
    checks["Required Features"] = any(f.lower() == "readme.md" for f in files)

    if stack == "Laravel":
        checks["API Availability"] = os.path.isdir(os.path.join(repo_path, "routes"))
        checks["Database Connectivity"] = os.path.isdir(os.path.join(repo_path, "database"))
        checks["Authentication Flow"] = os.path.isdir(os.path.join(repo_path, "app", "Http", "Controllers"))

    elif stack == "MERN":
        checks["API Availability"] = "package.json" in files
        pkg_deps = _read_package_json_deps(repo_path)
        checks["Database Connectivity"] = (
            os.path.exists(os.path.join(repo_path, "models")) or "mongoose" in pkg_deps or "mongodb" in pkg_deps
        )
        checks["Authentication Flow"] = _search_text_in_repo(repo_path, ["jwt", "passport", "auth", "bcrypt"])

    elif stack == "Python":
        checks["API Availability"] = _search_text_in_repo(
            repo_path, ["fastapi", "flask", "django", "quart", "falcon", "bottle", "starlette"]
        )
        checks["Database Connectivity"] = _search_text_in_repo(
            repo_path, ["sqlalchemy", "psycopg2", "pymongo", "sqlite3", "mysql", "redis", "motor", "asyncpg"]
        )
        checks["Authentication Flow"] = _search_text_in_repo(
            repo_path, ["jwt", "oauth", "login", "bcrypt", "passlib", "python-jose"]
        )

    else:
        checks["API Availability"] = False
        checks["Database Connectivity"] = False
        checks["Authentication Flow"] = False

    checks["Error Handling"] = _search_text_in_repo(repo_path, ["try:", "except", "try {", "catch"])
    checks["Security Configuration"] = ".env.example" in files or ".gitignore" in files

    return checks


def _read_package_json_deps(repo_path: str) -> str:
    """Parses package.json dependencies and devDependencies into a single lowercased string."""
    path = os.path.join(repo_path, "package.json")
    if not os.path.exists(path):
        return ""

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            return " ".join(deps.keys()).lower()
    except Exception as e:
        logger.debug("Could not parse package.json at %s: %s", path, e)
        return ""


def _search_text_in_repo(repo_path: str, keywords: list, max_files: int = 200) -> bool:
    """Fast search for specific technical keywords within repository source files."""
    count = 0
    target_extensions = (".py", ".js", ".ts", ".php", ".dart", ".json")

    for root, dirs, files in os.walk(repo_path):
        # Efficiently exclude unnecessary directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for fname in files:
            if count >= max_files:
                return False

            if fname.endswith(target_extensions):
                file_path = os.path.join(root, fname)

                # Skip oversized files
                try:
                    if os.path.getsize(file_path) > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        if any(kw in content for kw in keywords):
                            return True
                except Exception as e:
                    logger.debug("Failed reading file for keyword search %s: %s", file_path, e)

                count += 1

    return False


def scan_for_exposed_secrets(repo_path: str) -> list:
    """Scans repository files for exposed credentials, tokens, and hardcoded private keys."""
    target_extensions = (".py", ".js", ".ts", ".env", ".php", ".json", ".yml", ".yaml")
    findings = set()

    for root, dirs, files in os.walk(repo_path):
        # Exclude internal & build folders from secret checks
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for fname in files:
            if fname.endswith(target_extensions):
                file_path = os.path.join(root, fname)

                # Skip large non-source files
                try:
                    if os.path.getsize(file_path) > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for pattern in SECRET_PATTERNS:
                            if re.search(pattern, content, re.IGNORECASE):
                                relative_path = os.path.relpath(file_path, repo_path)
                                findings.add(relative_path)
                                break
                except Exception as e:
                    logger.debug("Failed scanning secrets in file %s: %s", file_path, e)

    return sorted(list(findings))