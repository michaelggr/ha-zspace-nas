"""实体 ID 命名：统一 ``<domain>.zspace_<短英文>``，不再用汉语拼音。

背景
----
HA 默认按实体显示名生成 entity_id，我们的显示名是中文，于是 ID 变成
``sensor.ji_kong_jian_z4pro_cpu_shi_yong_lu``——写模板、写自动化都要猜拼音。

规则
----
    <domain>.zspace_[<设备段>_]<用途>

* 主机实体：``sensor.zspace_cpu_usage``；
* 存储池：``sensor.zspace_pool_sata11_usage``；
* 磁盘：``sensor.zspace_disk_zw61gzgt_temperature``；
* 容器：``switch.zspace_qbittorrent`` / ``binary_sensor.zspace_qbittorrent_running``。

显示名（friendly_name）保持中文不变，只有 entity_id 变。

默认不启用：既有安装的实体已经是拼音 ID，改名会让写在面板/自动化里的旧 ID 失效。
选项里打开 ``short_entity_ids`` 后，实体加入时自动迁移（注册表改名，唯一 ID、
历史、统计全部保留），关掉也不会改回去——ID 一旦迁移就是永久的。
"""

from __future__ import annotations

import hashlib
import logging
import re

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

#: 所有实体 ID 的公共前缀
ENTITY_ID_PREFIX = "zspace"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG = 28


def entity_id_slug(text: object, max_len: int = _MAX_SLUG) -> str:
    """转成 ASCII 小写下划线；中文等非 ASCII 字符会被丢弃。"""
    value = _SLUG_RE.sub("_", str(text or "").lower()).strip("_")
    if len(value) > max_len:
        value = value[:max_len].rstrip("_")
    return value


def short_hash(text: object, length: int = 6) -> str:
    """名称全是非 ASCII（如中文容器名）时的稳定兜底段。"""
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:length]


def object_id(*parts: object) -> str:
    """把若干段拼成 ``a_b_c``，自动跳过空段。"""
    tokens = [entity_id_slug(part) for part in parts]
    return "_".join(token for token in tokens if token)


def token_or_hash(text: object, length: int = 6) -> str:
    """优先用 slug，全被过滤掉时退回稳定哈希，避免 ID 变成空段。"""
    return entity_id_slug(text) or short_hash(text, length)


class ForcedEntityIdMixin:
    """把实体 ID 固定成 ``<domain>.zspace_<短英文>``（显示名不动）。

    用法：子类在 ``__init__`` 里设置 ``self.entity_id``（由
    :meth:`_setup_forced_entity_id` 完成），旧 ID 由
    :meth:`async_migrate_entity_id` 在实体加入后迁移。
    """

    _forced_entity_id: str = ""
    #: 子类声明自己属于哪个平台（sensor / binary_sensor / switch / button）
    _platform_domain: str = ""

    #: 由宿主（ZspaceEntity）决定是否启用短 ID
    def _short_ids_enabled(self) -> bool:
        return False

    def _entity_id_parts(self) -> tuple[str, ...]:
        """子类覆盖：返回实体 ID 的英文段。"""
        return ()

    def _setup_forced_entity_id(self, platform_domain: str, *parts: object) -> None:
        """首次创建时直接给定 entity_id；已注册的旧 ID 靠迁移改名。"""
        if not platform_domain or not self._short_ids_enabled():
            return
        suffix = object_id(*parts)
        if not suffix:
            return
        self._forced_entity_id = f"{platform_domain}.{ENTITY_ID_PREFIX}_{suffix}"
        self.entity_id = self._forced_entity_id

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_migrate_entity_id()

    @callback
    def async_migrate_entity_id(self) -> None:
        """把注册表里的旧 ID（拼音）改成固定 ID，幂等。

        目标 ID 被别人占用时退到 ``<目标>_2``、``_3``…，绝不抢号。
        """
        target = getattr(self, "_forced_entity_id", "")
        current = self.entity_id
        hass = getattr(self, "hass", None)
        if not target or not current or current == target or hass is None:
            return

        registry = er.async_get(hass)
        occupant = registry.async_get(target)
        if occupant is None:
            new_id = target
        elif current.startswith(f"{target}_"):
            # 已经是 <目标>_N 的兜底形态，别来回改名
            return
        else:
            new_id = ""
            for index in range(2, 100):
                candidate = f"{target}_{index}"
                if registry.async_get(candidate) is None:
                    new_id = candidate
                    break
            if not new_id:
                _LOGGER.warning(
                    "极空间实体 %s 的目标 ID %s 已被占用且无可用兜底，保留原样",
                    current,
                    target,
                )
                return
            _LOGGER.info(
                "极空间实体 %s 的目标 ID 被 %s 占用，暂用 %s",
                current,
                occupant.entity_id,
                new_id,
            )
        try:
            registry.async_update_entity(current, new_entity_id=new_id)
        except (ValueError, KeyError) as err:
            _LOGGER.warning("极空间实体 %s 改名为 %s 失败：%s", current, new_id, err)
            return
        # 同一生命周期内的后续代码要拿到新 ID
        self.entity_id = new_id
        _LOGGER.info("极空间实体 ID 已迁移：%s → %s", current, new_id)
