import time
import logging
import psutil

logger = logging.getLogger(__name__)

# Store startup time
START_TIME = time.time()

# Store previous network counters
_last_net = psutil.net_io_counters()


def get_system_stats() -> dict:
    """
    Returns current system resource statistics.

    Used by:
    - Dashboard
    - Admin monitoring
    - Health endpoint
    """

    global _last_net

    try:
        cpu_percent = psutil.cpu_percent(interval=0.2)

        memory = psutil.virtual_memory()

        disk = psutil.disk_usage("/")

        current_net = psutil.net_io_counters()

        bytes_sent = current_net.bytes_sent - _last_net.bytes_sent
        bytes_recv = current_net.bytes_recv - _last_net.bytes_recv

        _last_net = current_net

        network_kbps = round(
            (bytes_sent + bytes_recv) / 1024,
            2,
        )

        uptime_seconds = int(time.time() - START_TIME)

        return {
            "cpu_percent": round(cpu_percent, 1),

            "cpu_cores": psutil.cpu_count(),

            "mem_used_mb": round(memory.used / (1024 * 1024), 1),

            "mem_available_mb": round(
                memory.available / (1024 * 1024),
                1,
            ),

            "mem_total_mb": round(
                memory.total / (1024 * 1024),
                1,
            ),

            "mem_percent": round(memory.percent, 1),

            "disk_used_gb": round(
                disk.used / (1024 ** 3),
                2,
            ),

            "disk_total_gb": round(
                disk.total / (1024 ** 3),
                2,
            ),

            "disk_percent": round(
                disk.percent,
                1,
            ),

            "network_kbps": network_kbps,

            "uptime_seconds": uptime_seconds,
        }

    except Exception as e:
        logger.exception("Failed to collect system statistics")

        return {
            "cpu_percent": 0,
            "cpu_cores": 0,

            "mem_used_mb": 0,
            "mem_available_mb": 0,
            "mem_total_mb": 0,
            "mem_percent": 0,

            "disk_used_gb": 0,
            "disk_total_gb": 0,
            "disk_percent": 0,

            "network_kbps": 0,

            "uptime_seconds": 0,

            "error": str(e),
        }