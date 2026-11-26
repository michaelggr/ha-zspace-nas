# 极空间 NAS for Home Assistant

把极空间（ZSpace）NAS 的状态接进 Home Assistant：系统负载、存储池容量、Docker 容器运行态，
并可选地控制容器 **启动 / 停止 / 重启**。

> 非官方集成。数据来自极空间官方 `zspace-cli`（Agent Skill 同源），接口未公开，
> 固件或 CLI 升级后可能需要适配。

## 能力

| 类别 | 内容 |
| --- | --- |
| 传感器 | CPU 使用率/温度、内存使用率/已用、网络收发、每个存储池的使用率/已用/可用（**属性里带成员盘**：槽位/型号/容量/温度/通电/重映射/健康）、容器总数与运行数、**每块盘的容量/温度/通电时间/重映射扇区**、**下载任务数/速度/做种数**、**外接 GPU 数量 / AI 任务数 / Time Machine 用量 / 系统事件数** |
| 二进制传感器 | 每容器运行态（含镜像、CPU、内存属性）、Docker 引擎状态、**每块盘的健康告警** |
| 控制（可选） | 每容器开关（start/stop）、重启按钮、`zspace_nas.container_action` 服务 |
| 运维（只读） | **外接 NVIDIA GPU 信息、本地 AI 任务（zllamas/zaispace）、Time Machine 备份状态、系统事件/登录日志**；不支持的机型自动隐藏，不会报错 |
| 事件播报（可选） | 新出现的「警告」及以上系统事件自动通过音箱播报（HA TTS），按 id 去重、60 秒冷却、防刷屏 |
| 设备 | NAS 一个设备，**每个容器一个子设备**（型号=镜像，带镜像/CPU/内存属性）、**每台虚拟机一个子设备**；填了「网页地址」后设备页出现「访问」链接 |
| 面板 | 侧边栏自动注册「极空间」入口，五个标签页（总览/容器/虚拟机/存储/**运维**），无需手动加卡片 |
| 调试 | `zspace_nas.dump_raw`：把任意能力的原始 JSON 落到 `/config/zspace_nas/` |

**刻意不做**：关机/重启、磁盘格式化、存储池删除、RAID 重建、防火墙修改、容器删除。
这些在 HA 里没有撤回路径，误触代价太高。

## 安全护栏

写操作是唯一可能弄坏东西的部分。默认状态下**一个开关都不会出现**：控制需要你点名授权。

| 层 | 拦什么 |
| --- | --- |
| 1. 白名单 | 只有写进「允许控制的容器」名单的容器才生成开关/按钮；名单为空 = 零控制面 |
| 2. 总开关 | 选项没开「允许容器控制」时，即使有实体也会被拒 |
| 3. 运行时护栏 | 受保护容器、状态不对（已运行还点启动）、冷却期内的重复操作 |
| 4. 客户端白名单 | 只有 `docker containers` 的 `start`/`stop`/`restart` 能进写通道，且调用点必须显式 `write=True` |

**配置方式**：集成 → 配置 → 「允许控制的容器」填逗号分隔的名称片段，例如
`qbittorrent, xiaoya, moviepilot`；填 `*` 表示全部允许（受保护的系统容器仍会被拒）。
重新加载后只对命中的容器生成开关。

**默认受保护**：名字含 `zspace` / `znas` / `zido` / `zvm` / `zapp` / `zconnect` / `zdrive`
的容器（极空间系统容器），写进白名单也会被拒。注意：很多机型并不用这类命名，
默认名单可能一个都命中不了；真正的风险是数据库、代理、内网隧道这类关键业务容器，
**程序无法自动判定，需要你自己别写进白名单**。

**永远不开放**：删除容器、强制停止、Docker 引擎开关/重启、镜像删除、Compose 删除、
NAS 关机/重启、磁盘格式化、存储池删除、RAID 重建、防火墙改配置。

**演练模式**：开启后所有开关动作只写日志、不发命令，适合先观察几天。

**冷却**：同一容器两次操作默认间隔 15 秒，防止脚本或连点反复重启。

被拦截时直接报原因：

```
已拦截：qbittorrent 不在允许控制的名单里（选项 → 允许控制的容器）
已拦截：zspace-video 属于受保护容器，禁止停止
已拦截：jellyfin 操作过于频繁，请 12 秒后再试
```

## 虚拟机（只读）

走 `ops vm` 这个只读能力（`risk=read`），**不碰 vm-manage**，所以不会开机、关机、改配置。

真机（ZOS 4.x）字段：`name` / `title` / `osType` / `cpuNum` / `memGbNum` /
`cpuPercent` / `memPercent` / `state`（1=运行、5=关闭）/ `stateLang` / `vncPort`。
列表接口没有磁盘容量和 VNC 地址，协调器会逐台补一次详情（`vm_id=<name>`）。

每台虚拟机一个设备，实体：

| 实体 | 说明 |
| --- | --- |
| 运行中（二进制） | 电源状态；属性带 os_type / firmware / uuid / vnc_port / **vnc_endpoints** / cpu / 内存 / 磁盘 |
| CPU 使用率、内存使用率 | 来自 `cpuPercent` / `memPercent`，百分数 |
| 虚拟 CPU、内存、磁盘容量 | 核数 / GiB / GiB（磁盘取 `sdaList[].virtualsize`） |
| VNC 端口 | 极空间分配的端口；`vnc_endpoints` 属性里是 `IP:端口` 地址列，**纯 TCP VNC** |

轮询默认 2 分钟（选项可调）。

**还没做**：开机 / 关机 / 重启。这些要走 `vm-manage`（destructive 域）。另外注意：
如果某个虚拟机就是你的 Home Assistant 本体，关机等于把自己关掉——所以这块要单独
设计白名单和二次确认，不能跟容器一样一键开关。

## 虚拟机控制台（可选）

极空间给的 VNC 是**裸 TCP**（详情里 `vnc` 是 `["192.168.1.10:15566", …]`），浏览器连不上。
所以集成在 HA 里做了一层 **WebSocket → TCP 桥**，前端用打包进来的 noVNC（+690 KB）。

打开方式：选项里勾选「虚拟机控制台」→ 重新加载 → 虚拟机设备页右上角出现「访问」，
点开就是画面（可全屏、可发 Ctrl+Alt+Del、可缩放）。

- **链接即凭证**：`/zspace_nas/vnc/<令牌>`，HMAC 签名 + 12 小时过期，改一个字节就失效；
  集成每 6 小时自动续签，设备页的链接不会忽然打不开。别把链接贴到公开的地方。
- **VNC 密码**在页面里输入，不落盘、不进日志；`vncPasswordEnable` 在 CLI 里也是脱敏的。
- **地址选择**：默认从地址列里挑一个非内部网段（跳过 `10.222.x.x` 那类桥接地址）；
  机器有多网卡、挑错了就在选项里填「VNC 主机」强制指定。
- **随时取新链接**：

  ```yaml
  service: zspace_nas.vnc_url
  data: {vm: HomeAssistant, hours: 12}
  ```

  响应里就是新链接（「开发者工具 → 操作 → 查看响应」）。

电源操作（开/关/重启）仍未实现——见上一节的风险说明。

## 容器实体自动跟随

Docker 每轮刷新（默认 30 秒）后会比对容器清单：

- **新增容器**：自动补建运行态传感器与开关，不用重载集成；
- **删除容器**：对应实体自动移除，设备一并清理；
- 新容器若命中控制白名单才建开关，没命中就只建监控实体。

以前新增容器要手动重载，现在最多等一个轮询周期。

## 设备访问链接

选项里填「极空间网页地址」（例如 `http://192.168.1.10:5055`），NAS 设备页右上角会出现「访问」，
一键跳到极空间自己的 Web 页面。留空则不显示，不影响其它功能。

## 实体 ID 命名（可选）

默认沿用 HA 的规则：显示名是中文，entity_id 就是拼音——`sensor.ji_kong_jian_z4pro_cpu_shi_yong_lu`。
写模板时很难用。

选项里打开「短英文实体 ID」后，实体会自动迁移成：

| 类型 | 之前 | 之后 |
| --- | --- | --- |
| 主机 | `sensor.ji_kong_jian_z4pro_cpu_shi_yong_lu` | `sensor.zspace_cpu_usage` |
| 存储池 | `sensor.…_cun_chu_chi_sata11_shi_yong_lu` | `sensor.zspace_pool_sata11_usage` |
| 磁盘 | `sensor.…_ci_pan_zw61gzgt_wen_du` | `sensor.zspace_disk_zw61gzgt_temperature` |
| 容器运行态 | `binary_sensor.qbittorrent_yun_xing_zhong` | `binary_sensor.zspace_qbittorrent_running` |
| 容器开关 | `switch.qbittorrent` | `switch.zspace_qbittorrent` |

**显示名不变**，只有 entity_id 变。迁移走实体注册表改名，唯一 ID、历史与统计都保留；
目标 ID 被占用时自动退到 `_2`、`_3`，不会抢别人的号。

⚠️ 两点要注意：

1. **不可逆**——ID 改完就是永久的，关掉选项不会改回去。
2. **面板/自动化里的旧 ID 会失效**，需要同步更新。用 auto-entities 按集成过滤的地方不受影响，
   写死 entity_id 的卡片（apexcharts、mushroom、bar-card）要一处处换。

## 安装

**HACS**：HACS → 集成 → 右上角三点 → 自定义存储库 → 粘贴
`https://github.com/michaelggr/ha-zspace-nas`，类别选 Integration → 安装 → 重启。

**HA 2026.11+（Marketplace）**：Settings → Marketplace → Add from link → 粘贴同一个地址。

**手动**：把 `custom_components/zspace_nas` 整个目录复制到 `/config/custom_components/`，或用
`dist/ha-zspace-nas-<version>.zip` 解压到 `/config`。

> HACS 只装代码。CLI 二进制与登录态仍需按上面「前置条件」自行准备。

## 配置

设置 → 设备与服务 → 添加集成 → 搜索「极空间」：

| 字段 | 说明 |
| --- | --- |
| CLI 可执行文件路径 | 默认 `/config/zspace_nas/bin/zspace-cli` |
| 登录态目录 | 默认 `/config/zspace_nas/profile` |
| Profile 名称 | 与电脑上登录时一致，默认 `ha` |
| NAS 局域网地址 | 可选。留空走云代理；填 `https://IP:5055` 走局域网直连 |
| 允许 HTTP 明文 | 仅当设备没有可信证书时使用，令牌会暴露在链路上 |

保存时会执行一次探活（`system info`），成功才创建条目。

**开启容器控制**：集成页面 → 配置 → 勾选「允许容器控制」→ 提交（会自动重载）。
未开启时不会生成开关和按钮，防止误触。

## 进阶：手动准备（可选）

一般不需要。以下情况才手动处理：

- 想换掉内置二进制（比如自己编译的版本）→ 在集成配置里填 CLI 路径；
- 想把某台机器上已有的登录态复用过来 → 把该目录整个复制到「登录态目录」，
  这样添加集成时不会弹授权步骤。

登录态目录结构：`<登录态目录>/profiles/<profile 名>/{config,credentials,session-authority}.json`。

## 服务

```yaml
service: zspace_nas.container_action
data:
  container_id: jellyfin        # 容器 ID、短 ID 或名称
  action: restart               # start | stop | restart
```

```yaml
service: zspace_nas.container_logs
data:
  container_id: qbittorrent     # 容器 ID、短 ID 或名称
  save: true                    # 可选：同时写文件
```

日志是**最近 100 行的快照**（后端限制），不是实时流。开了 `save` 会落到
`/config/zspace_nas/logs-<容器名>-<时间>.log`，方便下载或给自动化读取。
响应里直接带 `text`，在「开发者工具 → 操作 → 查看响应」能看到。

```yaml
service: zspace_nas.dump_raw
data:
  domain: storage
  cli_action: info
  params: {}
```

`dump_raw` 会把原始 JSON 写到 `/config/zspace_nas/dump-*.json`，用于在固件字段变化时核对结构。

```yaml
service: zspace_nas.announce_test
data:
  message: 极空间播报测试：音箱连接正常
```

`announce_test` 测音箱是否配置正确（留空播放器则用集成选项里填的那个）。面板右上角的小喇叭按钮就是它。

## 系统事件音箱播报

在集成选项里打开「系统事件音箱播报」并选一个 `media_player` 实体，之后：
- 新出现的**警告及以上**事件会通过 HA 的 TTS 播报（级别门槛可选 info/warning/error）；
- **只在 HA 里配好语音（TTS）才能出声**——播报走标准 `tts.speak` 服务，用媒体播放器绑定的默认引擎；
- 首次开启只记录现有事件当基线，**不会把历史全念一遍**；
- 同一事件只念一次，两次播报间隔至少 60 秒，一次最多拼 3 条，避免刷屏。

```yaml
# 想自定义播报文案/目标，也可以用自动化自己接
alias: 极空间事件播报到客厅
trigger:
  - platform: state
    entity_id: sensor.zspace_system_events   # 事件数变化
action:
  - service: zspace_nas.announce_test
    data:
      message: 极空间有新系统事件，请查看面板
      media_player: media_player.living_room
```

## 轮询

| 协调器 | 默认周期 | 内容 |
| --- | --- | --- |
| Docker | 30 s | 容器列表 + 全部容器统计 + 引擎状态 |
| 系统 | 60 s | CPU / 内存 / 网络 / 风扇 |
| 存储 | 300 s | 存储池容量 |
| 下载 | 300 s | 下载任务数与速度 |
| 磁盘 | 3600 s | 逐盘容量 + SMART（温度/通电/重映射/健康） |
| GPU / AI | 300 s | 外接 GPU 信息 + 本地 AI 任务（不支持机型自动跳过） |
| 系统事件 | 180 s | 系统事件/登录日志（播报按此周期检查新事件） |
| Time Machine | 900 s | 备份状态与配额用量 |

实测开销（x86 四盘位机型，60+ 容器，连续 6 分钟 48 次调用）：

| 项目 | 数值 |
| --- | --- |
| 总调用耗时 / 流量 | 26.3 秒 / 2.76 MB（占墙钟 7.3%） |
| Docker 一轮（list+stats+status） | 1.48 秒 / 224 KB，其中 `list` 占 193 KB |
| 系统一轮 | 0.68 秒 / 11 KB |
| 存储一轮 | 0.45 秒 / 6.7 KB |
| SMART 三盘一轮 | 2.06 秒 / 33 KB |
| NAS 侧负载 | CPU 0.45% → 0.29%，温度 53℃ → 44℃（无可测影响） |

`docker containers list` 是大头，容器越多越重；想再省流量就把 Docker 周期调到 60 秒。

周期在选项里可调。一个协调器失败只影响它自己的实体，不会让整个集成变 `unavailable`。

## 排查

| 现象 | 处理 |
| --- | --- |
| 配置报「找不到可执行文件」 | 检查路径与 `chmod +x`，HAOS 里必须是 Linux 版本 |
| 配置报「登录态无效」 | 重新执行第 2 步，确认 profile 目录覆盖完整 |
| 实体变不可用 | 看日志里的 `E_*` 错误码；`E_PERMISSION` 多为账号对 Docker 无权限 |
| 容器实体没出现 | 容器是启动后新增的，重载集成即可；或检查是否开启了容器控制 |
| 报「已拦截：…受保护容器」 | 确实需要操作就把它从选项的保护名单里移除，但系统容器不建议动 |
| 报「演练模式：跳过」 | 到集成选项里关闭演练模式 |
| 字段解析不对 | 跑 `dump_raw` 把 JSON 发出来，按实际结构补别名 |

## 版本记录

- **v0.11.4** — 修复虚拟机「控制台」点不开（报「链接无效或已过期」）：集成删除重配后 entry_id 变化，缓存的签名链接永久失效。现在控制台按钮点击时实时调用 `vnc_url` 服务签发新链接，并用新标签页直接打开（与设备页「访问」一致）；后端聚合实体也改为实时签发，不再读可能过期的设备注册项。
- **v0.11.3** — 修复面板标签切换和所有操作按钮无响应的问题：事件监听挂在宿主上，Shadow DOM 会把 `e.target` 重定向成宿主本身，`closest('[data-act]')` 永远找不到按钮；改用 `composedPath()` 取真实节点，点击和搜索框输入一起修复。
- **v0.11.2** — 修复用户改名实体 ID 后面板显示「聚合实体未就绪」的问题：面板在默认 ID 之外，增加按聚合实体独有属性（`view` / `groups` / `classes` / `total`）自动识别的兜底逻辑，改名后无需手动配置。
- **v0.11.1** — 修复侧边栏面板从未注册成功的问题：HA 2026 移除了 `frontend_url`/`sidebar` 参数，旧参数抛 TypeError 被静默吞掉；改用 `add_extra_js_url` 注入自托管 JS + 新版面板参数，并把 JS 视图设为免鉴权（否则 module 请求 401）。升级并重启后左侧会出现集成自带的「极空间」面板（五个标签）；旧版手动 dashboard 不受影响，可自行删除。
- **v0.11.0** — 新增外接 GPU / 本地 AI 任务、Time Machine 备份状态、系统事件三类只读监控；系统事件新告警可经音箱 TTS 播报（按 id 去重 + 级别过滤 + 冷却防刷屏）；面板新增「运维」页，侧边栏五个标签；新增 `announce_test` 服务
- **v0.10.5** — 修复概览页存储模板 SecurityError（`list.append` 被 HA 沙箱禁用）
- **v0.10.4** — 虚拟机视图 filter 去重，去掉 entity_id 与属性双条件导致的重复卡片
- **v0.10.3** — 虚拟机视图运行状态卡片 filter 修复（按 `vm_name` 属性匹配）
- **v0.10.2** — VM 状态文案兜底 + 智能家居自动化方案
- **v0.10.0/0.10.1** — 聚合实体 + 专用面板卡片 + 侧边栏自动注册
- **v0.9.4** — 修复 noVNC 连不上（502）：VNC 桥改为逐个尝试所有候选地址（局域网优先、内部网段兜底），不再只用第一个不可达地址；面板「虚拟机」视图改用 entity_id 匹配，卡片正常显示
- **v0.9.3** — 修复 HA 2026.x 移除 `HomeAssistantView.self.hass` 导致 noVNC 控制台页面 500；改用 `request.app[KEY_HASS]`，新增视图静态护栏
- **v0.9.2** — 修复 `VmInfo` 未从 coordinator 转出导致 binary_sensor 导入失败、集成无法加载；新增跨模块导入静态护栏
- **v0.9.1** — 修复 v0.9.0 里 `vnc_url` 服务注册参数错位导致集成无法加载的问题；面板新增「虚拟机」视图
- **v0.9.0** — 虚拟机只读监控 + noVNC 控制台（签名链接、WS→TCP 透传、每 6h 自动续签）
- **v0.8.0** — 按真机字段校准虚拟机解析
- **v0.7.0** — 虚拟机只读监控
- **v0.6.x** — 短英文实体 ID、启动孤儿实体清理
- **v0.5.0** — 容器自动跟随、存储池成员盘
- **v0.4.x** — 容器日志、面板与运行时长修正

## 路线图

- [x] v0.1 CLI 通道：系统 / 存储 / Docker 监控 + 容器启停
- [x] v0.2 磁盘 SMART（温度/通电/重映射/健康）、下载任务、白名单控制
- [ ] v0.3 原生 HTTP 通道（去掉二进制依赖，纯 Python）
- [ ] 新增容器自动建实体（无需重载）
- [ ] 备份中心状态（当前账号下 `backup center list` 为空，等有备份任务再接）

## 许可

MIT

