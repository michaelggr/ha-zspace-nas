# 极空间控制面板卡片（zspace-card）

零依赖的 Home Assistant 自定义卡片：把整台极空间 NAS 装进一张卡，毛玻璃风格，
信息分三层——**重点**（CPU/内存/温度环形仪表 + 容器/VM/存储概览）、
**次重点**（存储池容量条）、**详情**（容器/虚拟机/存储分页）。

> 对比参考项目 fnOS：那边是「聚合实体 + room-elves-card 专用卡」，
> 本卡片不依赖 room-elves-card，自带样式与交互，主题可切，noVNC 悬浮窗可拖动。

## 前提

- 集成版本 ≥ 0.10.0（含聚合实体 `sensor.zspace_all_entities`）；侧边栏面板需 ≥ 0.10.1。
- 聚合实体属性 `view` 里有数据（集成加载后几秒即有）。
- 容器启停需在集成选项开启「允许容器控制」；VM 电源/重启同理。
- noVNC 控制台需在集成选项开启「虚拟机控制台」，且 VM 处于运行状态。

## 安装方式

### 方式一：侧边栏面板（推荐，零配置）

**无需拷 JS、无需注册资源、无需加卡片。** 集成在 v0.10.1 起自带这张卡片：

1. 通过 HACS 把集成升级到 **≥ 0.10.1**；
2. 重启 Home Assistant；
3. 左侧边栏自动出现「**极空间**」（NAS 图标），点进去就是完整控制面板。

原理：集成加载时用 HA 官方 `async_register_built_in_panel` 注册侧边栏页面，
卡片 JS 由集成自己的 HTTP 视图从 `custom_components/zspace_nas/panel.js` 提供，
全程不依赖 `/config/www/`，也不改你的任何仪表盘配置。卸载集成时入口自动移除。

> 面板与下方卡片是同一份 JS（`panel.js` 由 `zspace-card.js` 生成，只多一个
> `ha-panel-zspace` 入口），表现完全一致；主题切换、启停、noVNC 悬浮窗都能用。

### 方式二：放进任意仪表盘（可选）

如果你想把面板嵌到自建仪表盘里（而不是用侧边栏），才需要手动注册资源：

**1. 注册前端资源**

HA → 设置 → 仪表盘 → 右上角三个点 → **资源** → 添加资源：

```
URL: /api/zspace_nas/panel.js
类型: JavaScript 模块
```

（v0.10.1 起集成已自托管 JS，不用再复制到 `/config/www/`；老版本才需要
`/local/zspace-card.js?v=1.0.0`。）

**2. 加卡片**

任意仪表盘 → 编辑 → 添加卡片 → 手动选择卡片 → 搜索 `zspace`，或直接贴 YAML：

```yaml
type: custom:zspace-card
entity: sensor.zspace_all_entities   # 可省，自动发现
name: 极空间 NAS
theme: aurora                        # aurora / ocean / ember / forest
default_tab: overview                # overview / containers / vms / storage
show_all: false                      # 容器页是否默认展开全部（含已停止）
console_target: window               # window=悬浮窗 / tab=新标签页
```

点右上角调色板图标可在 4 套主题间切换（仅当前会话有效，想固定就改 `theme`）。

## 卡片能做什么

| 区域 | 内容 | 可操作 |
| --- | --- | --- |
| 顶部 | 主机型号/版本、健康徽章、主题切换 | 点徽章看异常盘数 |
| 环形 KPI | CPU / 内存占用率，超阈值变红 | — |
| 概览统计 | 容器运行/总数、VM 运行/总数、存储池、硬盘数 | — |
| 网络条 | 上下行实时速率 | — |
| 总览页 | 存储池容量条 + 系统详情 chip | — |
| 容器页 | 运行/停止列表，CPU、内存 | 点 ▶/■ 启停，搜索、展开全部 |
| 虚拟机页 | 每 VM 一张卡，CPU/内存/IP/VNC | 电源、重启、打开 noVNC 悬浮窗 |
| 存储页 | 存储池容量条 + 成员盘折叠表 | 展开看槽位/型号/温度/通电/健康 |

noVNC 悬浮窗：可拖动标题栏移动、可点全屏、可关闭；多台 VM 的窗口互不遮挡。
若 HA 走反代导致 WebSocket 不通，在集成选项里填 `vnc_host` 为 HA 可达的 NAS IP。

## 数据结构（卡片读什么）

卡片只读聚合实体的 `attributes.view`，结构（由 `aggregate.py` 定型）：

```
view.host          {cpu,memory,temp,fan,net_rx,net_tx,download,...} 每个含 value/raw/unit/icon/entity_id
view.storage.pools [{name,usage,used,free,type,disks:[{pos,model,temp,health,healthy,...}]}]
view.containers    [{name,state,running,cpu,memory_mb,image,switch_id,action_button_id}]
view.vms           [{name,title,running,cpu,memory,ip,vnc_port,os,console_url,...}]
view.overview      {containers_total,running,vms_total,pools,disks,disks_bad,host_name,model,version}
```

全部字段归一化（单位、图标、运行态语义），前端零计算直接渲染。
新增容器/虚拟机由聚合实体自动跟随，卡片无需改配置。

## 自测截图

`tests/fixtures/shot_*.png` 是本地用假数据渲染的四个标签页预览，
`preview.html` 可在浏览器里交互（切主题、切标签、点启停）验证。
`panel_preview.png` 是侧边栏面板模式的渲染效果（由 `python tests/make_panel_preview.py` 生成）。
