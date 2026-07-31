import docker
import git
import shutil
import os
import re
import time
import tempfile

client = docker.from_env()

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


def clone_repository(github_url: str) -> str:
    temp_dir = tempfile.mkdtemp(prefix="sandbox_")
    git.Repo.clone_from(github_url, temp_dir, depth=1)
    return temp_dir


def run_in_container(repo_path: str, stack: str, timeout_seconds: int = 60):
    """
    Quick build/launch check. This container never needs internet access —
    it only confirms the base image can mount and read the repo — so
    network access is fully disabled here (network_disabled=True).
    """
    image = BASE_IMAGES.get(stack, "python:3.11-slim")
    start_time = time.time()
    logs = []
    stats_snapshot = {"cpu_percent": 0, "mem_usage_mb": 0}
    success = True

    try:
        client.images.pull(image)
    except Exception as e:
        logs.append(f"Image pull warning: {e}")

    container = None
    try:
        container = client.containers.run(
            image,
            command="sleep 30",
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
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
            cpu_percent = (cpu_delta / system_delta) * 100 if system_delta > 0 else 0
            mem_mb = stats["memory_stats"].get("usage", 0) / (1024 * 1024)
            stats_snapshot = {"cpu_percent": round(cpu_percent, 2), "mem_usage_mb": round(mem_mb, 2)}
        except Exception:
            pass

        logs.append(f"Container {container.short_id} started on image {image}")

    except Exception as e:
        success = False
        logs.append(f"Container execution failed: {e}")

    finally:
        if container:
            try:
                container.stop(timeout=5)
                container.remove(force=True)
            except Exception:
                pass

    build_time = time.time() - start_time
    return {
        "success": success,
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


def run_real_tests(repo_path: str, stack: str, timeout_seconds: int = HARD_TIMEOUT_SECONDS) -> dict:
    """
    Executes the project's real test suite inside an isolated Docker
    container. This container DOES need network access (to install
    dependencies), so instead of disabling the network entirely, it is
    locked down with: dropped Linux capabilities, no-new-privileges,
    a hard process-count limit, and a hard wall-clock timeout that force-
    kills the container if it's exceeded — untrusted code cannot hang the
    evaluation pipeline or exhaust host resources indefinitely.
    """
    image = BASE_IMAGES.get(stack, "python:3.11-slim")

    if stack == "Python":
        shell_cmd = (
            "pip install --quiet --no-cache-dir -r requirements.txt 2>/dev/null; "
            "pip install --quiet --no-cache-dir pytest 2>/dev/null; "
            "pytest --tb=no -q 2>&1 | tail -n 100"
        )
    elif stack == "MERN":
        shell_cmd = (
            "npm install --silent --no-audit --no-fund 2>/dev/null; "
            "npm test --silent 2>&1 | tail -n 100"
        )
    else:
        return {
            "executed": False,
            "reason": f"Automatic test execution is not supported for stack '{stack}' in this build. "
                      f"Falling back to static test-file detection."
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
            result = container.wait(timeout=timeout_seconds)
            exit_code = result.get("StatusCode", 1)
        except Exception:
            # Wall-clock timeout hit — force-kill immediately rather than
            # letting an untrusted process keep running on the host.
            timed_out = True
            try:
                container.kill()
            except Exception:
                pass
            exit_code = 124  # conventional "timed out" exit code

        output = container.logs().decode(errors="ignore")

    except Exception as e:
        output = f"Test execution error: {e}"
        exit_code = 1
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass

    if timed_out:
        return {
            "executed": False,
            "reason": f"Test execution exceeded the {timeout_seconds}s safety limit and was terminated.",
            "output_tail": output[-1000:],
        }

    if stack == "MERN" and "missing script" in output.lower():
        return {
            "executed": False,
            "reason": "No 'test' script defined in package.json.",
            "output_tail": output[-1000:],
        }

    if stack == "Python" and exit_code == 5:
        return {
            "executed": False,
            "reason": "No tests were collected by pytest.",
            "output_tail": output[-1000:],
        }

    if stack == "Python" and exit_code in (2, 3, 4):
        return {
            "executed": False,
            "reason": f"Test runner exited with an environment error (code {exit_code}), not a test failure.",
            "output_tail": output[-1000:],
        }

    passed, failed = _parse_pytest_summary(output) if stack == "Python" else (None, None)

    return {
        "executed": True,
        "exit_code": exit_code,
        "success": exit_code == 0,
        "output_tail": output[-2000:],
        "passed_count": passed,
        "failed_count": failed,
    }


def cleanup_repo(repo_path: str):
    shutil.rmtree(repo_path, ignore_errors=True)