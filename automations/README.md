# 极空间 × 智能家居 自动化联动

基于本集成提供的实体，把 NAS 的状态变成家里可感知、可联动的信号。全部用 HA 原生自动化，无需额外插件。

> **实体 ID 提示**：如果你开了「短英文实体 ID」，用下面 `sensor.zspace_*` 这种 ID；如果没开（还是拼音 ID），在
> 开发者工具 → 状态里搜中文名（如「下载任务」「CPU 使用率」）拿实际 ID 替换。虚拟机/容器的 ID 里的机器名以你 HA 里实际的为准，
> 用「开发者工具 → 状态」搜 `zspace_vm` / `zspace_` 确认。

## 实体速查（自动化会用到的）

| 用途 | 实体（短 ID 示例） | 说明 |
| --- | --- | --- |
| VM 运行态 | `binary_sensor.zspace_vm_<名>_running` | on/off，属性含 `ip`/`cpu_percent`/`memory_percent`/`vnc_endpoints` |
| Docker 引擎 | `binary_sensor.zspace_docker_running` | on/off |
| CPU 使用率 | `sensor.zspace_cpu_usage` | % |
| CPU 温度 | `sensor.zspace_cpu_temperature` | °C |
| 内存使用率 | `sensor.zspace_memory_usage` | % |
| 网络收/发 | `sensor.zspace_network_rx` / `_tx` | B/s |
| 下载任务/速度 | `sensor.zspace_downloads_active` / `_rate` | 个 / B/s |
| 做种任务 | `sensor.zspace_downloads_seeding` | 个 |
| 存储池使用率 | `sensor.zspace_pool_<key>_usage` | % |
| 磁盘温度/健康 | `sensor.zspace_disk_<sn>_temperature` / `_reallocated` | °C / 扇区数 |
| 聚合实体 | `sensor.zspace_all_entities` | 属性 `view` 含整台 NAS 定型数据，卡片读它 |

---

## 场景一：VM 开关机 → 家里灯光/状态反馈（最实用）

极空间的 VM 运行态是现成的二值传感器，最适合做联动。下面几个思路挑着用：

### 1. 家里有人玩 VM（win10）时，书房灯常亮、勿扰

```yaml
alias: 极空间-VM开机进入勿扰
mode: single
trigger:
  - platform: state
    entity_id: binary_sensor.zspace_vm_win10_running  # 换成你的 VM 实体
    from: "off"
    to: "on"
action:
  - service: light.turn_on
    target:
      entity_id: light.study_room  # 换成你的灯
  - service: input_boolean.turn_on
    target:
      entity_id: input_boolean.do_not_disturb
```

### 2. VM 全部关机 → 自动关灯、空调（人走了）

```yaml
alias: 极空间-所有VM关机离家联动
mode: single
trigger:
  - platform: state
    entity_id:
      - binary_sensor.zspace_vm_win10_running
      - binary_sensor.zspace_vm_homeassistant_running
    to: "off"
condition:
  # 所有 VM 都关了才触发
  - condition: state
    entity_id: binary_sensor.zspace_vm_win10_running
    state: "off"
  - condition: state
    entity_id: binary_sensor.zspace_vm_homeassistant_running
    state: "off"
action:
  - service: light.turn_off
    target:
      entity_id: light.study_room
  - service: climate.turn_off
    target:
      entity_id: climate.study_ac
```

> 注意：**别把承载 HA 自己的那台 VM 设为自动关机**——关了 HA 就断了，自动化也执行不了。
> 只联动其它 VM（win10、fn）。

---

## 场景二：温度/健康异常 → 手机报警（保护 NAS）

### 3. CPU 或磁盘过热 → 推送 + 语音播报

```yaml
alias: 极空间-过热告警
mode: throttled        # 避免反复刷屏
trigger:
  - platform: numeric_state
    entity_id: sensor.zspace_cpu_temperature
    above: 70
    for:
      minutes: 3
  - platform: numeric_state
    entity_id: sensor.zspace_disk_zw61gzgt_temperature  # 换成你的磁盘实体
    above: 50
    for:
      minutes: 5
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "⚠️ 极空间过热"
      message: >-
        {{ trigger.to_state.name }} 达到 {{ trigger.to_state.state }}{{ trigger.to_state.attributes.unit_of_measurement }}
  - service: tts.cloud_say
    data:
      message: "注意，极空间温度过高"
      entity_id: media_player.your_speaker
```

### 4. 磁盘重映射扇区增加 → 立即告警（硬盘要挂的前兆）

```yaml
alias: 极空间-磁盘健康告警
mode: single
trigger:
  - platform: numeric_state
    entity_id: sensor.zspace_disk_zw61gzgt_reallocated
    above: 0
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "🚨 磁盘健康告警"
      message: "检测到重映射扇区，建议尽快备份并更换硬盘"
```

---

## 场景三：下载/存储 → 生活节奏联动

### 5. 下载完成（任务数归零）→ 播报 / 灯绿一下

```yaml
alias: 极空间-下载完成提醒
mode: single
trigger:
  - platform: numeric_state
    entity_id: sensor.zspace_downloads_active
    below: 1
    for:
      seconds: 30
condition:
  # 避免 NAS 刚启动/无任务时误报：上次任务数 > 0 才提醒（可用 input_number 记录）
  - condition: numeric_state
    entity_id: input_number.last_download_count
    above: 0
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "✅ 下载完成"
      message: "极空间的下载任务都跑完了"
  - service: input_number.set_value
    target:
      entity_id: input_number.last_download_count
    data:
      value: 0
```

> 更稳的做法：装一个 `input_number` 记录上次任务数，用自动化在任务数 > 0 时更新它，归零时比对。

### 6. 存储池快满 → 提前提醒清理

```yaml
alias: 极空间-存储池将满
mode: throttled
trigger:
  - platform: numeric_state
    entity_id: sensor.zspace_pool_sata11_usage
    above: 85
    for:
      hours: 1
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "💾 极空间存储告警"
      message: "存储池使用率超过 85%，该清理了"
```

---

## 场景四：把 NAS 状态变成家里的"氛围灯"

### 7. CPU 高负载 → 灯带变红（用 RGB 灯做负载指示）

```yaml
alias: 极空间-负载氛围灯
mode: single
trigger:
  - platform: numeric_state
    entity_id: sensor.zspace_cpu_usage
    above: 80
    for:
      minutes: 2
    id: busy
  - platform: numeric_state
    entity_id: sensor.zspace_cpu_usage
    below: 30
    for:
      minutes: 2
    id: idle
action:
  - choose:
      - conditions: "{{ trigger.id == 'busy' }}"
        sequence:
          - service: light.turn_on
            target:
              entity_id: light.nas_indicator
            data:
              rgb_color: [255, 60, 60]
              brightness_pct: 60
      - conditions: "{{ trigger.id == 'idle' }}"
        sequence:
          - service: light.turn_on
            target:
              entity_id: light.nas_indicator
            data:
              rgb_color: [60, 200, 120]
              brightness_pct: 30
```

### 8. 半夜 NAS 还在下载 → 提醒（噪音/电费）

```yaml
alias: 极空间-夜间下载提醒
mode: single
trigger:
  - platform: numeric_state
    entity_id: sensor.zspace_downloads_active
    above: 0
    for:
      minutes: 5
condition:
  - condition: time
    after: "23:30"
    before: "07:00"
action:
  - service: notify.mobile_app_your_phone
    data:
      message: "半夜还在下载，要不要暂停？"
```

---

## 场景五：语音/助手查询（配合 HA 的语音助手）

把聚合实体的数据做成一句话播报。例如问"极空间状态"：

```yaml
alias: 极空间-语音状态播报
mode: single
trigger:
  - platform: conversation
    command:
      - "极空间状态"
      - NAS 状态
action:
  - service: tts.cloud_say
    data:
      entity_id: media_player.your_speaker
      message: >-
        极空间 CPU 占用 {{ states('sensor.zspace_cpu_usage') }}%，
        内存 {{ states('sensor.zspace_memory_usage') }}%，
        正在下载 {{ states('sensor.zspace_downloads_active') }} 个任务，
        磁盘温度 {{ states('sensor.zspace_disk_zw61gzgt_temperature') }} 度。
```

---

## 关于"虚拟机运行状态为空"

卡片/实体里 VM 的运行状态来自极空间 `ops vm` 接口，真机实测：
- `cpuPercent=0`、`memPercent=41`、`domainState=1`（运行）——**这些有值**；
- 但 `title`/`stateLang`/`ip`/`vncPort` 列表接口常常没有，所以之前版本会显示成机器名 `zspace_xxx`、状态文案空、IP/VNC 显示 `—`。

**已在 v0.10.2 修复展示层**：状态为空时按运行态显示"运行中/已关闭"，机器名去掉 `zspace_` 前缀。
想要显示 `HomeAssistant`/`win10` 这种自定义名，需要在极空间 App 里给 VM 设置备注/标题——接口里没有的字段，集成无法凭空生成。

`ip` 为空是因为 IP 在 VM 详情接口里，列表接口不给；要看 IP 请开虚拟机控制台，或装了 guest agent 后从路由器/HA 的设备追踪拿。
