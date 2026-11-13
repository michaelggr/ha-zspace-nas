"""极空间 NAS 集成的常量定义。"""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "zspace_nas"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button"]

# ── 配置项 ──────────────────────────────────────────────
CONF_BINARY = "binary_path"
CONF_PROFILE_HOME = "profile_home"
CONF_PROFILE = "profile"
CONF_HOST = "host"
CONF_ALLOW_INSECURE = "allow_insecure_http"
CONF_ALLOW_CONTROL = "allow_control"
CONF_DRY_RUN = "dry_run"
CONF_COOLDOWN = "action_cooldown"
CONF_PROTECTED = "protected_containers"
CONF_ALLOWED = "allowed_containers"
CONF_DOCKER_INTERVAL = "docker_interval"
CONF_SYSTEM_INTERVAL = "system_interval"
CONF_STORAGE_INTERVAL = "storage_interval"
CONF_DISK_INTERVAL = "disk_interval"
CONF_DOWNLOAD_INTERVAL = "download_interval"

# 二进制随集成下发，留空即用内置（按 CPU 架构自动选 x64/arm64）；
# 登录态目录放 /config 下，跨升级保留。
DEFAULT_BINARY = ""
DEFAULT_PROFILE_HOME = "/config/zspace_nas/profile"
DEFAULT_PROFILE = "ha"

# ── 轮询周期：分级，避免一个慢接口拖垮全部实体 ──────────
DEFAULT_DOCKER_INTERVAL = 30
DEFAULT_SYSTEM_INTERVAL = 60
DEFAULT_STORAGE_INTERVAL = 300
DEFAULT_DISK_INTERVAL = 3600   # SMART 逐盘查询，慢，按小时
DEFAULT_DOWNLOAD_INTERVAL = 300

MIN_INTERVAL = 10
MAX_INTERVAL = 3600

DEFAULT_TIMEOUT = 40  # 单条 CLI 命令超时（秒）

# ── 高危动作护栏 ────────────────────────────────────────
DEFAULT_COOLDOWN = 15  # 同一容器两次操作的最小间隔（秒）
MIN_COOLDOWN = 0
MAX_COOLDOWN = 600

# ── 服务 ────────────────────────────────────────────────
SERVICE_CONTAINER_ACTION = "container_action"
SERVICE_DUMP_RAW = "dump_raw"

ATTR_CONTAINER_ID = "container_id"
ATTR_ACTION = "action"
ATTR_DOMAIN = "domain"
ATTR_CLI_ACTION = "cli_action"
ATTR_PARAMS = "params"

ALLOWED_CONTAINER_ACTIONS = ("start", "stop", "restart")

INTERVAL_HELPERS = {
    CONF_DOCKER_INTERVAL: (DEFAULT_DOCKER_INTERVAL, "docker"),
    CONF_SYSTEM_INTERVAL: (DEFAULT_SYSTEM_INTERVAL, "system"),
    CONF_STORAGE_INTERVAL: (DEFAULT_STORAGE_INTERVAL, "storage"),
}


def seconds(value: int) -> timedelta:
    """把秒数转成 HA 轮询需要的 timedelta。"""
    return timedelta(seconds=value)
