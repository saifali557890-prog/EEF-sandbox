import os
import logging

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 1024 * 1024  # 1 MB

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


def run_test_suite(repo_path: str, stack: str) -> dict:
    """
    Performs lightweight static detection of testing-related components.
    """

    has_unit = _has_test_files(repo_path)
    has_api = _has_api_definition(repo_path, stack)
    has_db = _has_db_config(repo_path)

    return {
        "api": has_api,
        "unit": has_unit,
        "db": has_db,
        "integration": has_api and has_unit,
        "uismoke": _has_ui(repo_path),
        "performance": has_unit,
    }


def _walk_repository(repo_path):
    """
    Generator that walks the repository while skipping ignored folders.
    """
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        yield root, dirs, files


def _has_test_files(repo_path: str) -> bool:
    """
    Detects common unit/integration test directories and files.
    """

    test_dirs = {
        "tests",
        "test",
        "__tests__",
        "spec",
    }

    test_suffixes = (
        "_test.py",
        "_spec.js",
        ".spec.js",
        ".test.js",
        ".spec.ts",
        ".test.ts",
    )

    for root, dirs, files in _walk_repository(repo_path):

        for d in dirs:
            if d.lower() in test_dirs:
                return True

        for f in files:

            if f.endswith(test_suffixes):
                return True

            if "test" in f.lower():
                return True

    return False


def _has_api_definition(repo_path: str, stack: str) -> bool:
    """
    Searches source code for common backend API definitions.
    """

    keywords = {
        "Python": [
            "@app.get",
            "@app.post",
            "@app.put",
            "@app.delete",
            "@router.get",
            "@router.post",
            "FastAPI(",
            "Flask(",
        ],
        "MERN": [
            "app.get(",
            "app.post(",
            "router.get(",
            "router.post(",
            "express(",
        ],
        "Laravel": [
            "Route::get",
            "Route::post",
            "Route::resource",
        ],
    }.get(stack, [])

    extensions = (
        ".py",
        ".js",
        ".ts",
        ".php",
    )

    for root, _, files in _walk_repository(repo_path):

        for file in files:

            if not file.endswith(extensions):
                continue

            path = os.path.join(root, file)

            try:

                if os.path.getsize(path) > MAX_FILE_SIZE:
                    continue

                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read().lower()

                if any(k.lower() in content for k in keywords):
                    return True

            except Exception as e:
                logger.debug("Unable to inspect %s : %s", path, e)

    return False


def _has_db_config(repo_path: str) -> bool:
    """
    Detects common database configuration files/folders.
    """

    indicators = {
        ".env.example",
        ".env",
        "schema.prisma",
        "models",
        "migrations",
        "database",
    }

    for root, dirs, files in _walk_repository(repo_path):

        for d in dirs:
            if d in indicators:
                return True

        for f in files:
            if f in indicators:
                return True

    return False


def _has_ui(repo_path: str) -> bool:
    """
    Detects frontend/UI projects.
    """

    ui_dirs = {
        "public",
        "src",
        "templates",
        "frontend",
        "client",
    }

    for root, dirs, _ in _walk_repository(repo_path):

        for d in dirs:
            if d.lower() in ui_dirs:
                return True

    return False