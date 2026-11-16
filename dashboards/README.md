# 极空间面板

一套 Lovelace 配置，四个视图，已按 `sections` 版式排版（HA 2024.3+ 的官方新版式，自适应手机与桌面）。

| 视图 | 内容 |
| --- | --- |
| 概览 | 四个 KPI 磁贴（CPU / 内存 / 温度 / 风扇）、容器与磁盘摘要、三个存储池进度条、磁盘明细（折叠）、网络吞吐曲线、下载概况 |
| 容器 | 左「运行中」/ 右「已停止」两组磁贴墙，点一下启停，长按看详情；已停止默认折叠 |
| 趋势 | CPU 与内存、存储池使用率、磁盘温度、网络吞吐，24 小时曲线 |
| 全部实体 | 传感器 / 二进制传感器 / 容器开关，按类型分栏列出 |

## 依赖的前端卡片

面板按你实际装的卡片选型，全部来自 HACS 前端：

| 卡片 | 用在哪 |
| --- | --- |
| `mushroom` | KPI 磁贴、容器磁贴（图标 + 名称 + 状态） |
| `apexcharts-card` | 网络吞吐、CPU、存储池、磁盘温度曲线 |
| `bar-card` | 存储池使用率进度条（70% 转黄、85% 转红） |
| `auto-entities` | 容器磁贴墙按状态自动分组、实体清单 |
| `expander-card` | 「磁盘明细」「已停止」折叠区 |
| `multiple-entity-row` | 磁盘行里并排显示温度 / 通电 / 重映射 |

没装 `card-mod` 也能用——本面板没有用任何 CSS 注入，全部走原生主题变量。

## 导入方式

**方式一：UI 新建（推荐）**

1. 设置 → 仪表盘 → 添加仪表盘 → 标题 `极空间`、URL `zspace`、选「从空白开始」
2. 打开 → 铅笔 → 三个点 → 原始配置编辑器
3. 粘贴 `dashboards/zspace/dashboard.yaml` 全文 → 保存

**方式二：YAML 模式（侧边栏固定入口）**

1. 把 `dashboard.yaml` 复制到 `/config/zspace/dashboard.yaml`
2. 把 `dashboards/sidebar.yaml` 里的 `lovelace:` 段贴进 `/config/configuration.yaml`
3. 重启 HA

## 设计说明

- 版式用 `sections` 而不是默认的 masonry：masonry 会把每张卡塞进独立列，卡片少时会留下大片空白，且单卡宽度被硬性限制在约 420px。
- `mini-graph-card` 在 `sections` 的固定高度里曲线会被裁切，所以 KPI 用 mushroom 磁贴，曲线统一放「趋势」页由 apexcharts 承担。
- `auto-entities` 要用网格展示自定义卡片时，必须写 `card_param: cards`，并把卡片模板放在 filter 的 `options` 里；只写 `card.cards` 会渲染为空。

## 前提

- 集成已添加（设备与服务里能看到「极空间」）。
- 想在容器页启停：「集成 → 配置」勾选「允许容器控制」，「允许控制的容器」填 `*` 或具体名字。
  不填就只有监控，容器页会是空的。
- 容器磁贴依赖集成重启后才出现的 `switch` 实体；刚添加集成时如果只看到监控数据，重载一次集成即可。

## 调整建议

- **磁贴列数**：容器页两个 section 里 `card: {type: grid, columns: 3}`，改成 2 会更宽松、4 会更紧凑。
- **存储池阈值**：`bar-card` 的 `severity` 里改 `from/to`，目前是 0–70 绿、70–85 黄、85–100 红。
- **想加新容器分组**：复制一个 `custom:auto-entities`，把 `filter.include` 换成 `name: "*关键词*"`。
