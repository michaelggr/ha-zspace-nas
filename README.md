# 极空间 NAS for Home Assistant

把极空间（ZSpace）NAS 的状态接进 Home Assistant：系统负载、存储池容量、Docker 容器运行态，
并可选地控制容器 **启动 / 停止 / 重启**。

> 非官方集成。数据来自极空间官方 `zspace-cli`（Agent Skill 同源），接口未公开，
> 固件或 CLI 升级后可能需要适配。

## 能力

| 类别 | 内容 |
| --- | --- |
| 传感器 | CPU 使用率/温度、内存使用率/已用、网络收发、每个存储池的使用率/已用/可用、容器总数与运行数、**每块盘的容量/温度/通电时间/重映射扇区**、**下载任务数/速度/做种数** |
| 二进制传感器 | 每容器运行态（含镜像、CPU、内存属性）、Docker 引擎状态、**每块盘的健康告警** |
| 控制（可选） | 每容器开关（start/stop）、重启按钮、`zspace_nas.container_action` 服务 |
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

## 前置条件

集成本身不实现登录，复用官方 CLI 的登录态。三步：

这两步分别是：**二进制**是真正去跟 NAS 通信的程序，**登录态**是它已经拿到的通行证。
HACS 只装 Python 代码，这两样必须自己放进去，放一次以后不用再动。

### 1. 放二进制

```bash
# 在能联网的机器上，或用 tools/fetch_cli.py 自动按平台下载并校验 sha256
python tools/fetch_cli.py --dest ./bin
```

把对应平台的二进制放到 HA 的 `/config/zspace_nas/bin/zspace-cli` 并加执行权限：

```bash
mkdir -p /config/zspace_nas/bin
chmod +x /config/zspace_nas/bin/zspace-cli
```

CLI 是静态链接的，HAOS（Alpine）里可直接运行。x86_64 用 `linux-x64`，树莓派/ARM NAS 用 `linux-arm64`。

### 2. 电脑上授权一次

```bash
export ZSPACE_CLI_HOME=./zprofile
./zspace-cli --profile ha login
```

浏览器完成授权后，CLI 会把登录态写进 `./zprofile`（文件后端，权限 0600）。

### 3. 搬登录态

把整个 `zprofile` 目录复制到 `/config/zspace_nas/profile`：

```bash
# 通过 Samba / SSH 插件等方式复制，最终结构应为：
# /config/zspace_nas/profile/session-authority.json 等
```

Token 由 CLI 自己刷新，搬到 HA 之后不需要再登录。

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

## 服务

```yaml
service: zspace_nas.container_action
data:
  container_id: jellyfin        # 容器 ID、短 ID 或名称
  action: restart               # start | stop | restart
```

```yaml
service: zspace_nas.dump_raw
data:
  domain: storage
  cli_action: info
  params: {}
```

`dump_raw` 会把原始 JSON 写到 `/config/zspace_nas/dump-*.json`，用于在固件字段变化时核对结构。

## 轮询

| 协调器 | 默认周期 | 内容 |
| --- | --- | --- |
| Docker | 30 s | 容器列表 + 全部容器统计 + 引擎状态 |
| 系统 | 60 s | CPU / 内存 / 网络 / 风扇 |
| 存储 | 300 s | 存储池容量 |
| 下载 | 300 s | 下载任务数与速度 |
| 磁盘 | 3600 s | 逐盘容量 + SMART（温度/通电/重映射/健康） |

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

## 路线图

- [x] v0.1 CLI 通道：系统 / 存储 / Docker 监控 + 容器启停
- [x] v0.2 磁盘 SMART（温度/通电/重映射/健康）、下载任务、白名单控制
- [ ] v0.3 原生 HTTP 通道（去掉二进制依赖，纯 Python）
- [ ] 新增容器自动建实体（无需重载）
- [ ] 备份中心状态（当前账号下 `backup center list` 为空，等有备份任务再接）

## 许可

MIT
