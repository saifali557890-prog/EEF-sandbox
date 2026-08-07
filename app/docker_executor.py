import docker
import git
import shutil
import os
import re
import time
import tempfile
import logging

# Configure logger for sandbox executor
logger = logging.getLogger(__name__)

BASE_IMAGES = {
    "Python": "python:3.11-slim",
    "MERN": "node:20-slim",
    "Laravel": "php:8.2-cli",
    "Flutter": "python:3.11-slim",
    "Unknown": "python:3.11-slim",
}

# Hard safety ceiling — no evaluation is allowed to run longer than this,
# regardless of what the repo's own code does (protects against infinite
# loops / resource exhaustion from an untrusted submission).
HARD_TIMEOUT_SECONDS = 90


def get_docker_client():
    """
    Safely returns a Docker client.
    Checks reliable Railway environment variables first. If running on Railway
    or if the host machine lacks a Docker daemon, returns None instead of crashing.
    """
    if os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RAILWAY_ENVIRONMENT_NAME"):
        return None

    try:
        return docker.from_env()
    except Exception as e:
        logger.warning(f"Docker unavailable: {e}")
        return None


def clone_repository(github_url: str) -> str:
    """
    Clones a remote repository into a temporary directory safely.
    Raises RuntimeError if cloning fails instead of crashing unhandled.
    """
    temp_dir = tempfile.mkdtemp(prefix="sandbox_")
    try:
        # Depth 1 prevents heavy history downloads for large repos
        git.Repo.clone_from(github_url, temp_dir, depth=1)
        return temp_dir
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"Repository clone failed for {github_url}: {e}")
        raise RuntimeError(f"Repository clone failed: {e}") from e


def run_in_container(repo_path: str, stack: str, timeout_seconds: int = 60):
    """
    Quick build/launch check. Uses local image cache first before pulling
    to avoid unnecessary network latency on every evaluation request.
    """
    client = get_docker_client()
    if client is None:
        return {
            "success": False,
            "docker_skipped": True,
            "logs": [
                "Docker daemon is unavailable or execution was skipped in cloud environment."
            ],
            "build_time_seconds": 0,
            "stats": {"cpu_percent": 0, "mem_usage_mb": 0},
        }

    image = BASE_IMAGES.get(stack, "python:3.11-slim")
    start_time = time.time()
    logs = []
    stats_snapshot = {"cpu_percent": 0, "mem_usage_mb": 0}
    success = True

    # Smart image caching: Check local image first before pulling
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        try:
            client.images.pull(image)
        except Exception as e:
            logs.append(f"Image pull warning: {e}")
            logger.warning(f"Image pull failed for {image}: {e}")
    except Exception as e:
        logs.append(f"Image retrieval warning: {e}")
        logger.warning(f"Image retrieval check failed: {e}")

    container = None
    try:
        container = client.containers.run(
            image,
            command=f"sleep {timeout_seconds}",  # Dynamic timeout parameter used
            volumes={repo_path: {"bind": "/workspace", "mode": "ro"}},
            working_dir="/workspace",
            detach=True,
            mem_limit="512m",
            nano_cpus=1_000_000_000,
            network_disabled=True,          # no internet needed for this check
            cap_drop=["ALL"],               # drop all Linux capabilities
            security_opt=["no-new-privileges"],
            pids_limit=128,                 # prevent fork-bomb style abuse
        )

        try:
            stats = container.stats(stream=False)
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"]
                - stats["precpu_stats"]["system_cpu_usage"]
            )
            cpu_percent = (
                (cpu_delta / system_delta) * 100 if system_delta > 0 else 0
            )
            mem_mb = stats["memory_stats"].get("usage", 0) / (1024 * 1024)
            stats_snapshot = {
                "cpu_percent": round(cpu_percent, 2),
                "mem_usage_mb": round(mem_mb, 2),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch container stats: {e}")

        logs.append(f"Container {container.short_id} started on image {image}")

    except Exception as e:
        success = False
        logs.append(f"Container execution failed: {e}")
        logger.error(f"Container execution failed: {e}")

    finally:
        if container:
            try:
                container.stop(timeout=5)
                container.remove(force=True)
            except Exception as e:
                logger.warning(f"Container cleanup warning: {e}")

    build_time = time.time() - start_time
    return {
        "success": success,
        "docker_skipped": False,
        "logs": logs,
        "build_time_seconds": round(build_time, 2),
        "stats": stats_snapshot,
    }


def _parse_pytest_summary(output: str):
    passed_match = re.search(r"(\d+)\s+passed", output)
    failed_match = re.search(r"(\d+)\s+failed", output)
    passed = int(passed_match.group(1)) if passed_match else None
    failed = int(failed_match.group(1)) if failed_match else 0
    return passed, failed


def run_real_tests(
    repo_path: str, stack: str, timeout_seconds: int = HARD_TIMEOUT_SECONDS
) -> dict:
    """
    Executes the project's real test suite inside an isolated Docker container.
    """
    client = get_docker_client()
    if client is None:
        return {
            "executed": False,
            "docker_skipped": True,
            "reason": "Docker daemon is unavailable on this cloud environment.",
            "output_tail": "",
        }

    image = BASE_IMAGES.get(stack, "python:3.11-slim")

    # Smart command chain: exit immediately with non-zero status if pip/npm fails
    if stack == "Python":
        shell_cmd = (
            "pip install --quiet --no-cache-dir -r requirements.txt || exit 1; "
            "pip install --quiet --no-cache-dir pytest || exit 1; "
            "pytest --tb=no -q 2>&1 | tail -n 100"
        )
    elif stack == "MERN":
        shell_cmd = (
            "npm install --silent --no-audit --no-fund || exit 1; "
            "npm test --silent 2>&1 | tail -n 100"
        )
    else:
        return {
            "executed": False,
            "docker_skipped": False,
            "reason": f"Automatic test execution is not supported for stack '{stack}' in this build. "
                      f"Falling back to static test-file detection.",
        }

    container = None
    output = ""
    exit_code = None
    timed_out = False

    try:
        container = client.containers.run(
            image,
            command=["sh", "-c", shell_cmd],
            volumes={repo_path: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            detach=True,
            mem_limit="768m",
            nano_cpus=1_000_000_000,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=256,
        )

        try:
            # Backward compatibility for Docker SDK versions without timeout in wait()
            try:
                result = container.wait(timeout=timeout_seconds)
            except TypeError:
                result = container.wait()
            exit_code = result.get("StatusCode", 1)
        except Exception as e:
            timed_out = True
            logger.warning(f"Test container execution timed out/failed: {e}")
            try:
                container.kill()
            except Exception as kill_err:
                logger.warning(f"Failed to kill timed out container: {kill_err}")
            exit_code = 124

        # Safe log parsing and decoding
        logs_bytes = container.logs()
        if isinstance(logs_bytes, bytes):
            output = logs_bytes.decode("utf-8", errors="ignore")
        else:
            output = str(logs_bytes)

    except Exception as e:
        output = f"Test execution error: {e}"
        logger.error(f"Test container run failed: {e}")
        exit_code = 1
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception as e:
                logger.warning(f"Cleanup warning for test container: {e}")

    if timed_out:
        return {
            "executed": False,
            "docker_skipped": False,
            "reason": f"Test execution exceeded the {timeout_seconds}s safety limit and was terminated.",
            "output_tail": output[-1000:],
        }

    if stack == "MERN" and "missing script" in output.lower():
        return {
            "executed": False,
            "docker_skipped": False,
            "reason": "No 'test' script defined in package.json.",
            "output_tail": output[-1000:],
        }

    if stack == "Python" and exit_code == 5:
        return {
            "executed": False,
            "docker_skipped": False,
            "reason": "No tests were collected by pytest.",
            "output_tail": output[-1000:],
        }

    if stack == "Python" and exit_code in (2, 3, 4):
        return {
            "executed": False,
            "docker_skipped": False,
            "reason": f"Test runner exited with an environment error (code {exit_code}), not a test failure.",
            "output_tail": output[-1000:],
        }

    passed, failed = (
        _parse_pytest_summary(output) if stack == "Python" else (None, None)
    )

    return {
        "executed": True,
        "docker_skipped": False,
        "exit_code": exit_code,
        "success": exit_code == 0,
        "output_tail": output[-2000:],
        "passed_count": passed,
        "failed_count": failed,
    }


def cleanup_repo(repo_path: str):
    """
    Safely cleans up temporary directories without raising unhandled exceptions.
    """
    try:
        shutil.rmtree(repo_path)
    except Exception as e:
        logger.warning(f"Repository cleanup failed for {repo_path}: {e}")