import os
import json
import logging

logger = logging.getLogger(__name__)

STACK_PRIORITY = [
    "Laravel",
    "Flutter",
    "MERN",
    "Python",
]


def detect_stack(repo_path: str) -> str:
    """
    Detects the primary technology stack of a repository.

    Returns:
        Laravel
        Flutter
        MERN
        Python
        Unknown
    """

    try:
        root_files = {
            f.lower()
            for f in os.listdir(repo_path)
        }
    except Exception as e:
        logger.exception("Unable to inspect repository: %s", e)
        return "Unknown"

    # ------------------------
    # Laravel
    # ------------------------
    if (
        "artisan" in root_files
        or "composer.json" in root_files
        or os.path.isdir(os.path.join(repo_path, "routes"))
    ):
        return "Laravel"

    # ------------------------
    # Flutter
    # ------------------------
    if (
        "pubspec.yaml" in root_files
        or os.path.isdir(os.path.join(repo_path, "lib"))
    ):
        return "Flutter"

    # ------------------------
    # Node / MERN
    # ------------------------
    if "package.json" in root_files:

        package_path = os.path.join(repo_path, "package.json")

        try:
            with open(
                package_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:
                package = json.load(f)

            deps = {}

            deps.update(package.get("dependencies", {}))
            deps.update(package.get("devDependencies", {}))

            deps = {k.lower() for k in deps.keys()}

            node_frameworks = {
                "express",
                "mongoose",
                "mongodb",
                "react",
                "next",
                "nestjs",
                "socket.io",
                "vite",
            }

            if deps.intersection(node_frameworks):
                return "MERN"

        except Exception:
            logger.debug("Unable to parse package.json")

        return "MERN"

    # ------------------------
    # Python
    # ------------------------
    python_files = {
        "requirements.txt",
        "pyproject.toml",
        "manage.py",
        "setup.py",
        "poetry.lock",
        "pipfile",
    }

    if root_files.intersection(python_files):
        return "Python"

    for root, _, files in os.walk(repo_path):

        if ".git" in root:
            continue

        for file in files:

            if file.endswith(".py"):
                return "Python"

    return "Unknown"