/**
 * 极空间 NAS 控制面板卡片 (zspace-card)
 *
 * 零依赖 HA 自定义卡片：只读传感器.zspace_all_entities 的 view 属性，
 * 一份数据整屏渲染。信息分三层——
 *   重点：顶部奶油色 KPI（CPU/内存/温度）环形仪表 + 概览条；
 *   次重点：存储池容量条；
 *   详情：容器 / 虚拟机 分页列表，可操作、可开 noVNC 悬浮窗。
 *
 * 不依赖 card-mod / mushroom / room-elves-card，样式全部内联在 shadow DOM。
 */
(() => {
  const VERSION = '1.0.0';

  const ICON = {
    cpu: 'mdi:cpu-64-bit', memory: 'mdi:memory', temp: 'mdi:thermometer',
    fan: 'mdi:fan', net: 'mdi:network', download: 'mdi:download',
    docker: 'mdi:docker', vm: 'mdi:monitor', disk: 'mdi:harddisk',
    pool: 'mdi:database', play: 'mdi:play', stop: 'mdi:stop',
    restart: 'mdi:restart', console: 'mdi:console', check: 'mdi:check-circle',
    alert: 'mdi:alert-circle', close: 'mdi:close', full: 'mdi:fullscreen',
    chevron: 'mdi:chevron-down', power: 'mdi:power', refresh: 'mdi:refresh',
    gpu: 'mdi:expansion-card', ai: 'mdi:brain', backup: 'mdi:backup-restore',
    bell: 'mdi:bell-outline', speaker: 'mdi:speaker',
  };

  const TABS = [
    { id: 'overview', label: '总览', icon: ICON.pool },
    { id: 'containers', label: '容器', icon: ICON.docker },
    { id: 'vms', label: '虚拟机', icon: ICON.vm },
    { id: 'storage', label: '存储', icon: ICON.disk },
    { id: 'ops', label: '运维', icon: ICON.gpu },
  ];

  const THEMES = {
    aurora: { name: '极光', accent: '#7c8cff', accent2: '#26d0ce', bg: 'rgba(18,22,34,.72)' },
    ocean:  { name: '深海', accent: '#29b6f6', accent2: '#26a69a', bg: 'rgba(10,26,38,.74)' },
    ember:  { name: '熔岩', accent: '#ff8a65', accent2: '#ffca28', bg: 'rgba(30,18,18,.74)' },
    forest: { name: '松林', accent: '#66bb6a', accent2: '#26a69a', bg: 'rgba(14,28,20,.74)' },
  };

  const fmtNum = (v, d = 1) => {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    return Number.isInteger(n) ? String(n) : n.toFixed(d);
  };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);

  const bytes = (v) => {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, x = n;
    while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
    return `${fmtNum(x, i ? 1 : 0)} ${u[i]}`;
  };

  /** 环形进度 SVG：重点指标用 */
  function ring(pct, color, label, sub) {
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    const r = 34, c = 2 * Math.PI * r, off = c * (1 - p / 100);
    return `<div class="ring">
      <svg viewBox="0 0 80 80" aria-label="${esc(label)}">
        <circle class="ring-bg" cx="40" cy="40" r="${r}"></circle>
        <circle class="ring-fg" cx="40" cy="40" r="${r}"
          stroke="${color}" stroke-dasharray="${c.toFixed(1)}"
          stroke-dashoffset="${off.toFixed(1)}"></circle>
      </svg>
      <div class="ring-center"><b>${fmtNum(pct, 0)}<small>%</small></b><span>${esc(sub || label)}</span></div>
    </div>`;
  }

  class ZspaceCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._hass = null;
      this._config = {};
      this._tab = 'overview';
      this._expanded = false;
      this._theme = 'aurora';
      this._filter = '';
      this._rendered = false;
      this._bind();
    }

    setConfig(config) {
      this._config = config || {};
      this._theme = this._config.theme && THEMES[this._config.theme]
        ? this._config.theme : 'aurora';
      this._tab = this._config.default_tab || 'overview';
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    getCardSize() { return 9; }

    _entity() {
      if (!this._hass) return null;
      const cfgId = this._config.entity;
      if (cfgId && this._hass.states[cfgId]) return this._hass.states[cfgId];
      // 兜底 1：默认实体 ID（被占号时带 _2 后缀）
      const key = Object.keys(this._hass.states).find(
        (id) => /^sensor\.zspace_all_entities(?:_\d+)?$/.test(id)
      );
      if (key) return this._hass.states[key];
      // 兜底 2：用户改过实体 ID 时，按聚合实体独有属性签名识别。
      // view/groups/classes/total 同时出现是极空间聚合实体的特征，避免误认其它传感器。
      return Object.values(this._hass.states).find((state) => {
        const attrs = state.attributes || {};
        return state.entity_id.startsWith('sensor.') &&
          attrs.view && typeof attrs.view === 'object' &&
          Array.isArray(attrs.groups) && attrs.classes &&
          Number.isFinite(Number(attrs.total));
      }) || null;
    }

    _bind() {
      // Shadow DOM 事件冒泡到宿主时会被重定向：e.target 变成宿主本身，
      // 必须用 composedPath() 取真实节点，否则标签/按钮点击全部失效。
      const fromPath = (e, predicate) => {
        const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
        return path.find(predicate) || null;
      };
      this.addEventListener('click', (e) => {
        const btn = fromPath(e, (n) => n && n.dataset && n.dataset.act);
        if (!btn) return;
        const act = btn.dataset.act;
        if (act === 'tab') {
          this._tab = btn.dataset.tab; this._filter = ''; this._expanded = false;
          this._render();
        } else if (act === 'theme') {
          const keys = Object.keys(THEMES);
          this._theme = keys[(keys.indexOf(this._theme) + 1) % keys.length];
          this._render();
        } else if (act === 'announce') {
          e.stopPropagation();
          this._testAnnounce(btn);
        } else if (act === 'expand') {
          this._expanded = !this._expanded; this._render();
        } else if (act === 'filter') {
          const v = (this.shadowRoot.getElementById('filter-input') || {}).value || '';
          this._filter = v.trim().toLowerCase(); this._render();
          const input = this.shadowRoot.getElementById('filter-input');
          if (input) { input.focus(); input.value = this._filter; }
        } else if (act === 'service') {
          e.stopPropagation();
          this._callService(btn.dataset, btn);
        } else if (act === 'console') {
          e.stopPropagation();
          this._openConsole(btn.dataset.url, btn.dataset.name);
        } else if (act === 'close-console') {
          const box = this.shadowRoot.getElementById('vnc-modal');
          if (box) box.remove();
        } else if (act === 'full-console') {
          const frame = this.shadowRoot.querySelector('#vnc-modal iframe');
          if (frame && frame.requestFullscreen) frame.requestFullscreen();
        } else if (act === 'drag-console') {
          this._startDrag(e, btn);
        }
      });
      this.addEventListener('input', (e) => {
        const input = fromPath(e, (n) => n && n.id === 'filter-input');
        if (input) {
          this._filter = input.value.trim().toLowerCase();
          this._renderList();
        }
      });
    }

    async _testAnnounce(btn) {
      if (!this._hass) return;
      const original = btn.innerHTML;
      btn.classList.add('busy'); btn.innerHTML = '…';
      try {
        await this._hass.callService('zspace_nas', 'announce_test', {
          message: '极空间播报测试：音箱连接正常',
        });
        btn.title = '已发送播报测试';
      } catch (err) {
        btn.title = '播报失败：' + String(err) + '（请先在集成选项里配置媒体播放器与 TTS）';
      } finally {
        btn.classList.remove('busy'); btn.innerHTML = original;
      }
    }

    async _callService(ds, btn) {
      if (!this._hass) return;
      const [domain, service] = (ds.service || '').split('.');
      if (!domain || !service) return;
      const original = btn.innerHTML;
      btn.classList.add('busy'); btn.innerHTML = '…';
      try {
        await this._hass.callService(domain, service, { entity_id: ds.entity });
      } catch (err) {
        btn.title = String(err);
      } finally {
        btn.classList.remove('busy'); btn.innerHTML = original;
      }
    }

    _openConsole(url, name) {
      if (!url) return;
      if (this._config.console_target === 'tab') { window.open(url, '_blank'); return; }
      const old = this.shadowRoot.getElementById('vnc-modal');
      if (old) old.remove();
      const wrap = document.createElement('div');
      wrap.id = 'vnc-modal'; wrap.className = 'vnc-modal';
      wrap.innerHTML = `<div class="vnc-bar" data-act="drag-console">
          <span class="vnc-dot"></span><b>${esc(name || '控制台')}</b>
          <span class="vnc-spacer"></span>
          <button data-act="full-console" title="全屏">${this._icon(ICON.full)}</button>
          <button data-act="close-console" title="关闭">${this._icon(ICON.close)}</button>
        </div>
        <iframe src="${esc(url)}" allow="clipboard-read; clipboard-write"></iframe>`;
      this.shadowRoot.appendChild(wrap);
      this._makeDraggable(wrap, wrap.querySelector('.vnc-bar'));
    }

    _startDrag(e, handle) { this._makeDraggable(this.shadowRoot.getElementById('vnc-modal'), handle, e); }

    _makeDraggable(box, handle, startEvent) {
      if (!box || !handle || box._dragInit) return;
      box._dragInit = true;
      handle.style.cursor = 'move';
      const move = (ev) => {
        if (!box._drag) return;
        box.style.left = `${Math.max(0, Math.min(window.innerWidth - 120, box._drag.x + ev.clientX - box._drag.sx))}px`;
        box.style.top = `${Math.max(0, Math.min(window.innerHeight - 60, box._drag.y + ev.clientY - box._drag.sy))}px`;
        box.style.right = 'auto'; box.style.bottom = 'auto';
      };
      const up = () => { box._drag = null; window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      handle.addEventListener('pointerdown', (ev) => {
        if (ev.target.closest('button')) return;
        const rect = box.getBoundingClientRect();
        box._drag = { x: rect.left, y: rect.top, sx: ev.clientX, sy: ev.clientY };
        box.style.left = `${rect.left}px`; box.style.top = `${rect.top}px`;
        box.style.right = 'auto'; box.style.bottom = 'auto';
        window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
      });
      if (startEvent) handle.dispatchEvent(new PointerEvent('pointerdown', {
        bubbles: true, clientX: startEvent.clientX, clientY: startEvent.clientY,
      }));
    }

    _icon(name, cls = '') {
      return `<ha-icon class="${cls}" icon="${name}"></ha-icon>`;
    }

    _view() {
      const ent = this._entity();
      return (ent && ent.attributes && ent.attributes.view) || null;
    }

    _render() {
      if (!this._hass) return;
      const view = this._view();
      const t = THEMES[this._theme];
      const root = this.shadowRoot;
      if (!view) {
        root.innerHTML = `<ha-card><div class="empty">
          ${this._icon(ICON.alert)}<div><b>极空间聚合实体未就绪</b>
          <p>请确认集成版本 ≥ 0.11.2；面板会自动识别改名后的聚合实体，若仍不可见请检查集成加载状态。</p></div></div></ha-card>`;
        this._injectStyle();
        return;
      }
      const ov = view.overview || {};
      root.innerHTML = `<ha-card>
        <div class="card" style="--accent:${t.accent};--accent2:${t.accent2};--glass:${t.bg}">
          <div class="aurora"></div>
          ${this._header(ov)}
          ${this._hero(view.host || {}, ov)}
          ${this._tabs()}
          <div class="panel" id="panel">${this._panel(view)}</div>
          <div class="foot">极空间控制面板 · v${VERSION} · 数据每 4 秒同步</div>
        </div>
      </ha-card>`;
      this._injectStyle();
      this._rendered = true;
      this._expanded = this._config.show_all === true;
    }

    _header(ov) {
      const bad = ov.disks_bad || 0;
      const health = bad ? `<span class="badge bad">${this._icon(ICON.alert)} ${bad} 盘异常</span>`
        : `<span class="badge ok">${this._icon(ICON.check)} 全部健康</span>`;
      return `<div class="top">
        <div class="brand">
          <div class="brand-ico">${this._icon('mdi:nas')}</div>
          <div><h1>${esc(ov.host_name || this._config.name || '极空间 NAS')}</h1>
            <div class="sub">${esc(ov.model || 'Zspace NAS')} ${ov.version ? '· ' + esc(ov.version) : ''}</div></div>
        </div>
        <div class="top-right">
          ${health}
          <button class="ico-btn" data-act="announce" title="播报测试（需先在集成选项配置音箱）">${this._icon(ICON.speaker)}</button>
          <button class="ico-btn" data-act="theme" title="切换主题">${this._icon('mdi:palette')}</button>
        </div>
      </div>`;
    }

    _hero(host, ov) {
      const cpu = host.cpu || {}, mem = host.memory || {}, temp = host.temp || {};
      const fan = host.fan || {}, nrx = host.net_rx || {}, ntx = host.net_tx || {};
      const cpuColor = (Number(cpu.raw) || 0) > 80 ? '#ff6b6b' : 'var(--accent)';
      const memColor = (Number(mem.raw) || 0) > 85 ? '#ff6b6b' : 'var(--accent2)';
      return `<div class="hero">
        <div class="rings">
          ${ring(cpu.raw, cpuColor, 'CPU', '处理器')}
          ${ring(mem.raw, memColor, '内存', '占用')}
          <div class="mini-stack">
            <div class="mini">${this._icon(ICON.temp)}<div><span>温度</span><b>${fmtNum(temp.value, 0)}${esc(temp.unit || '°C')}</b></div></div>
            <div class="mini">${this._icon(ICON.fan)}<div><span>风扇</span><b>${fmtNum(fan.value, 0)} <small>rpm</small></b></div></div>
          </div>
        </div>
        <div class="stats">
          <div class="stat"><b>${ov.containers_running ?? '—'}<small>/${ov.containers_total ?? '—'}</small></b><span>${this._icon(ICON.docker)} 容器</span></div>
          <div class="stat"><b>${ov.vms_running ?? '—'}<small>/${ov.vms_total ?? '—'}</small></b><span>${this._icon(ICON.vm)} 虚拟机</span></div>
          <div class="stat"><b>${ov.pools ?? '—'}</b><span>${this._icon(ICON.pool)} 存储池</span></div>
          <div class="stat"><b>${ov.disks ?? '—'}</b><span>${this._icon(ICON.disk)} 硬盘</span></div>
        </div>
        <div class="netline">
          <span>${this._icon('mdi:arrow-down')} ${fmtNum(nrx.value, 1)} ${esc(nrx.unit || '')}</span>
          <span>${this._icon('mdi:arrow-up')} ${fmtNum(ntx.value, 1)} ${esc(ntx.unit || '')}</span>
        </div>
      </div>`;
    }

    _tabs() {
      return `<div class="tabs">${TABS.map((tab) => `
        <button class="tab ${this._tab === tab.id ? 'active' : ''}" data-act="tab" data-tab="${tab.id}">
          ${this._icon(tab.icon)}<span>${tab.label}</span></button>`).join('')}</div>`;
    }

    _panel(view) {
      switch (this._tab) {
        case 'containers': return this._containers(view.containers || []);
        case 'vms': return this._vms(view.vms || []);
        case 'storage': return this._storage(view.storage || {});
        case 'ops': return this._ops(view);
        default: return this._overview(view);
      }
    }

    // 运维页：外接 GPU / 本地 AI 任务 + Time Machine 备份 + 系统事件
    _ops(view) {
      const gpu = view.gpu || {};
      const tm = view.time_machine || {};
      const events = view.events || [];
      const newCount = events.filter((e) => e.is_new).length;
      return `<div class="section">
        <div class="sec-title">${this._icon(ICON.gpu)} 外接 GPU 与 AI 任务</div>
        ${this._gpuBlock(gpu)}
        <div class="sec-title" style="margin-top:16px">${this._icon(ICON.backup)} Time Machine 备份</div>
        ${this._tmBlock(tm)}
        <div class="sec-title" style="margin-top:16px">${this._icon(ICON.bell)} 系统事件
          ${newCount ? `<span class="badge bad" style="margin-left:auto">${newCount} 条未读</span>` : ''}</div>
        ${this._eventBlock(events)}
      </div>`;
    }

    _gpuBlock(gpu) {
      const gpus = gpu.gpus || [];
      const tasks = gpu.tasks || [];
      if (gpu.available === false && !gpus.length && !tasks.length) {
        return '<div class="none">当前机型未提供外接 GPU / AI 接口（或接口不可用）</div>';
      }
      const gpuRows = gpus.map((g) => {
        const memPct = Math.max(0, Math.min(100, Number(g.memory_percent) || 0));
        return `<div class="gpu-card">
          <div class="gpu-head"><b>${this._icon(ICON.gpu)} ${esc(g.model || g.name || 'GPU')}</b>
            <span>${fmtNum(g.utilization, 0)}% 负载</span></div>
          <div class="bar"><i style="width:${memPct}%;background:${memPct > 85 ? '#ef5350' : 'var(--accent)'}"></i></div>
          <div class="pool-meta"><span>显存 ${fmtNum(g.memory_used_mib, 0)} / ${fmtNum(g.memory_total_mib, 0)} MiB</span>
            <span>${fmtNum(g.temperature, 0)}°C</span><span>${esc(g.driver || '')}</span></div>
        </div>`;
      }).join('');
      const taskRows = tasks.map((t) => `<div class="disk-row">
          <span class="model">${this._icon(ICON.ai)} ${esc(t.name)}</span>
          <span class="slot">${esc(t.process_name || '')}</span>
          <span class="temp">${esc(t.status || '运行中')}</span>
          <span class="life">${t.memory_mib ? fmtNum(t.memory_mib, 0) + ' MiB' : ''}</span>
        </div>`).join('');
      return `${gpuRows || '<div class="none">未检测到外接 GPU</div>'}
        ${tasks.length ? `<div class="sub-title">本地 AI 任务</div>${taskRows}` : ''}`;
    }

    _tmBlock(tm) {
      if (!tm || !tm.status && tm.enabled === null && !tm.path) {
        return '<div class="none">当前账号未配置 Time Machine</div>';
      }
      const u = Math.max(0, Math.min(100, Number(tm.usage) || 0));
      const state = tm.have_backup ? '已有备份' : (tm.enabled ? '已启用' : '未启用');
      return `<div class="gpu-card">
        <div class="gpu-head"><b>${this._icon(ICON.backup)} ${esc(state)}</b>
          <span>${tm.usage != null ? fmtNum(u, 1) + '%' : '无限制'}</span></div>
        <div class="bar"><i style="width:${u}%;background:var(--accent2)"></i></div>
        <div class="pool-meta"><span>已用 ${tm.used_gib != null ? fmtNum(tm.used_gib, 1) + ' GiB' : '—'}</span>
          <span>配额 ${tm.limit_gib != null ? fmtNum(tm.limit_gib, 1) + ' GiB' : '无限制'}</span>
          <span>${esc(tm.path || '')}</span></div>
      </div>`;
    }

    _eventBlock(events) {
      if (!events.length) return '<div class="none">暂无系统事件</div>';
      return `<div class="events">${events.map((e) => {
        const rank = ['info', 'warning', 'error'].includes(e.level) ? e.level : '';
        const color = e.level === 'error' ? 'bad' : (rank === 'warning' ? 'warn' : 'ok');
        return `<div class="event-row ${e.is_new ? 'is-new' : ''}">
          <span class="ev-dot ${color}"></span>
          <div class="ev-body"><b>${esc(e.title || e.type || '系统事件')}</b>
            <span>${esc(e.content || '')}</span></div>
          <div class="ev-meta"><span>${esc(e.level || '')}</span><span>${esc(e.time || '')}</span></div>
        </div>`;
      }).join('')}</div>`;
    }

    _overview(view) {
      const pools = (view.storage || {}).pools || [];
      const host = view.host || {};
      const extras = Object.keys(host).filter((k) => !['cpu', 'memory', 'temp', 'fan', 'net_rx', 'net_tx'].includes(k));
      return `<div class="section">
        <div class="sec-title">${this._icon(ICON.pool)} 存储池容量</div>
        ${pools.length ? pools.map((p) => this._poolRow(p)).join('') : '<div class="none">暂无存储数据</div>'}
        ${extras.length ? `<div class="sec-title" style="margin-top:14px">${this._icon('mdi:information-outline')} 系统详情</div>
        <div class="chips">${extras.map((k) => {
          const it = host[k];
          return `<div class="chip"><span>${esc(it.name || k)}</span><b>${fmtNum(it.value, 1)} ${esc(it.unit || '')}</b></div>`;
        }).join('')}</div>` : ''}
      </div>`;
    }

    _storage(storage) {
      const pools = storage.pools || [];
      if (!pools.length) return '<div class="none">暂无存储数据</div>';
      return `<div class="section">${pools.map((p) => `
        <div class="pool-card">
          ${this._poolRow(p)}
          <details class="disks"><summary>${this._icon(ICON.disk)} ${p.disks.length || 0} 块成员盘</summary>
            ${(p.disks || []).map((d) => `<div class="disk-row">
              <span class="slot">${esc(d.pos || '—')}</span>
              <span class="model">${esc(d.model || d.sn || '—')}</span>
              <span class="temp">${fmtNum(d.temperature, 0)}°C</span>
              <span class="life">${d.power_on_hours ?? '—'} h</span>
              <span class="health ${d.healthy ? 'ok' : 'bad'}">${d.healthy ? '正常' : esc(d.health || '异常')}</span>
            </div>`).join('') || '<div class="none">无成员盘详情</div>'}
          </details>
        </div>`).join('')}</div>`;
    }

    _poolRow(p) {
      const u = Math.max(0, Math.min(100, Number(p.usage) || 0));
      const color = u > 85 ? '#ef5350' : u > 70 ? '#ffb74d' : 'var(--accent2)';
      return `<div class="pool">
        <div class="pool-head"><b>${esc(p.name)}</b><span>${fmtNum(u, 1)}%</span></div>
        <div class="bar"><i style="width:${u}%;background:${color}"></i></div>
        <div class="pool-meta"><span>已用 ${bytes(p.used)}</span><span>可用 ${bytes(p.free)}</span><span>${esc(p.type || '')}</span></div>
      </div>`;
    }

    _containers(list) {
      const filtered = this._filter
        ? list.filter((c) => (c.name || '').toLowerCase().includes(this._filter)) : list;
      const shown = this._expanded ? filtered : filtered.filter((c) => c.running).concat(
        filtered.filter((c) => !c.running).slice(0, 5)
      );
      const more = filtered.filter((c) => !c.running).length > 5 && !this._expanded;
      return `<div class="section">
        <div class="list-tools">
          <div class="search">${this._icon('mdi:magnify')}<input id="filter-input" placeholder="筛选容器…" value="${esc(this._filter)}"></div>
          <button class="text-btn" data-act="filter">筛选</button>
          <button class="text-btn" data-act="expand">${this._expanded ? '收起' : `全部 (${list.length})`}</button>
        </div>
        <div class="rows" id="rows">${this._containerRows(shown)}</div>
        ${more ? '<div class="none">仅显示运行中与前 5 个已停止，点「全部」展开</div>' : ''}
      </div>`;
    }

    _containerRows(list) {
      if (!list.length) return '<div class="none">没有匹配的容器</div>';
      return list.map((c) => {
        const on = !!c.running;
        const act = on ? 'stop' : 'start';
        const svc = c.switch_id
          ? `switch.turn_${on ? 'off' : 'on'}|${c.switch_id}`
          : `button.press|${c.action_button_id || ''}`;
        return `<div class="row">
          <span class="dot ${on ? 'on' : ''}"></span>
          <div class="row-main"><b>${esc(c.name)}</b><span>${esc(c.image || c.state || '')}</span></div>
          <div class="row-metric"><span>CPU</span><b>${fmtNum(c.cpu, 1)}%</b></div>
          <div class="row-metric"><span>内存</span><b>${fmtNum(c.memory_mb, 0)} MB</b></div>
          <button class="op ${on ? 'stop' : 'start'}" data-act="service"
            data-service="${svc}" title="${on ? '停止' : '启动'}">
            ${this._icon(on ? ICON.stop : ICON.play)}</button>
        </div>`;
      }).join('');
    }

    _vms(list) {
      const filtered = this._filter
        ? list.filter((v) => (v.title || v.name || '').toLowerCase().includes(this._filter)) : list;
      return `<div class="section">
        <div class="list-tools">
          <div class="search">${this._icon('mdi:magnify')}<input id="filter-input" placeholder="筛选虚拟机…" value="${esc(this._filter)}"></div>
          <button class="text-btn" data-act="filter">筛选</button>
        </div>
        <div class="vm-grid" id="rows">${filtered.map((v) => {
          const on = !!v.running;
          const pwr = v.switch_id ? `switch.turn_${on ? 'off' : 'on'}|${v.switch_id}` : '';
          const reboot = v.reboot_button_id ? `button.press|${v.reboot_button_id}` : '';
          return `<div class="vm-card ${on ? 'on' : ''}">
            <div class="vm-head">
              <span class="vm-ico">${this._icon(ICON.vm)}</span>
              <div><b>${esc(v.title || v.name)}</b><span>${esc(v.os || '虚拟机')}</span></div>
              <span class="status ${on ? 'on' : ''}">${on ? '运行中' : '已关闭'}</span>
            </div>
            <div class="vm-metrics">
              <div><span>CPU</span><b>${fmtNum(v.cpu, 1)}%</b></div>
              <div><span>内存</span><b>${fmtNum(v.memory, 1)}%</b></div>
              <div><span>IP</span><b>${esc(v.ip || '—')}</b></div>
              <div><span>VNC</span><b>${v.vnc_port || '—'}</b></div>
            </div>
            <div class="vm-ops">
              ${pwr ? `<button class="op ${on ? 'stop' : 'start'}" data-act="service" data-service="${pwr}" title="${on ? '关机' : '开机'}">${this._icon(ICON.power)}</button>` : ''}
              ${on && reboot ? `<button class="op" data-act="service" data-service="${reboot}" title="重启">${this._icon(ICON.restart)}</button>` : ''}
              <button class="console-btn" data-act="console" data-url="${esc(v.console_url || '')}" data-name="${esc(v.title || v.name)}"
                ${on && v.console_url ? '' : 'disabled'}>${this._icon(ICON.console)} 控制台</button>
            </div>
          </div>`;
        }).join('') || '<div class="none">没有匹配的虚拟机</div>'}</div>
      </div>`;
    }

    /** 列表局部重绘：只更新 rows，避免输入框失焦 */
    _renderList() {
      const view = this._view();
      if (!view) return;
      const panel = this.shadowRoot.getElementById('panel');
      if (!panel) return;
      if (this._tab === 'containers') panel.innerHTML = this._containers(view.containers || []);
      else if (this._tab === 'vms') panel.innerHTML = this._vms(view.vms || []);
      const input = this.shadowRoot.getElementById('filter-input');
      if (input) { input.focus(); input.value = this._filter; }
    }

    _injectStyle() {
      if (this.shadowRoot.querySelector('style')) return;
      const style = document.createElement('style');
      style.textContent = CSS;
      this.shadowRoot.prepend(style);
    }

    static getStubConfig() {
      return { entity: 'sensor.zspace_all_entities', name: '极空间 NAS', theme: 'aurora' };
    }
  }

  const CSS = `
    :host { display:block; font-family: var(--paper-font-body1_-_font-family, system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif); }
    ha-card { overflow:hidden; border-radius:18px; }
    .card { position:relative; padding:18px 20px 12px; color:#eef1f7; background:var(--glass);
      backdrop-filter:blur(22px) saturate(150%); -webkit-backdrop-filter:blur(22px) saturate(150%);
      border:1px solid rgba(255,255,255,.10); box-shadow:0 10px 40px rgba(0,0,0,.28); }
    .aurora { position:absolute; inset:-40% -10% auto auto; width:360px; height:360px; border-radius:50%;
      background:radial-gradient(circle, var(--accent) 0%, transparent 62%); opacity:.20; pointer-events:none; filter:blur(18px); }
    .top { display:flex; justify-content:space-between; align-items:center; gap:12px; position:relative; }
    .brand { display:flex; align-items:center; gap:12px; min-width:0; }
    .brand-ico { width:44px; height:44px; border-radius:13px; display:grid; place-items:center;
      background:linear-gradient(135deg,var(--accent),var(--accent2)); box-shadow:0 6px 18px rgba(0,0,0,.3); flex:none; }
    .brand-ico ha-icon { --mdc-icon-size:26px; color:#fff; }
    h1 { font-size:19px; margin:0; font-weight:700; letter-spacing:.3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sub { font-size:11.5px; opacity:.6; margin-top:2px; }
    .top-right { display:flex; align-items:center; gap:8px; }
    .badge { font-size:11px; padding:5px 10px; border-radius:999px; display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }
    .badge ha-icon { --mdc-icon-size:14px; }
    .badge.ok { background:rgba(38,166,154,.18); color:#5ee0c8; }
    .badge.bad { background:rgba(239,83,80,.18); color:#ff8a80; }
    .ico-btn, .text-btn { background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.12); color:#eef1f7;
      border-radius:9px; cursor:pointer; padding:6px 10px; font-size:12px; transition:.15s; }
    .ico-btn:hover, .text-btn:hover { background:rgba(255,255,255,.16); }
    .text-btn { padding:6px 12px; }
    .hero { display:grid; grid-template-columns: 1.4fr 1fr; gap:16px; margin:18px 0 14px; position:relative; }
    .rings { display:flex; align-items:center; gap:14px; }
    .ring { position:relative; width:92px; height:92px; flex:none; }
    .ring svg { width:100%; height:100%; transform:rotate(-90deg); }
    .ring circle { fill:none; stroke-width:7; }
    .ring-bg { stroke:rgba(255,255,255,.10); }
    .ring-fg { stroke-linecap:round; transition:stroke-dashoffset .6s ease; }
    .ring-center { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }
    .ring-center b { font-size:21px; line-height:1; }
    .ring-center b small { font-size:11px; opacity:.7; margin-left:1px; }
    .ring-center span { font-size:10px; opacity:.6; margin-top:3px; }
    .mini-stack { display:flex; flex-direction:column; gap:10px; flex:1; }
    .mini { display:flex; align-items:center; gap:10px; padding:10px 12px; border-radius:12px;
      background:rgba(255,255,255,.055); border:1px solid rgba(255,255,255,.08); }
    .mini ha-icon { --mdc-icon-size:20px; color:var(--accent2); }
    .mini div { display:flex; flex-direction:column; }
    .mini span { font-size:10.5px; opacity:.6; }
    .mini b { font-size:16px; }
    .mini b small { font-size:10px; opacity:.6; }
    .stats { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    .stat { padding:12px 14px; border-radius:13px; background:rgba(255,255,255,.055);
      border:1px solid rgba(255,255,255,.08); display:flex; flex-direction:column; justify-content:center; }
    .stat b { font-size:21px; line-height:1.1; }
    .stat b small { font-size:12px; opacity:.55; }
    .stat span { font-size:11px; opacity:.65; margin-top:4px; display:flex; align-items:center; gap:4px; }
    .stat span ha-icon { --mdc-icon-size:13px; }
    .netline { grid-column:1/-1; display:flex; gap:24px; font-size:12px; opacity:.8; padding-top:2px; }
    .netline span { display:inline-flex; align-items:center; gap:5px; }
    .netline ha-icon { --mdc-icon-size:14px; color:var(--accent2); }
    .tabs { display:flex; gap:6px; border-bottom:1px solid rgba(255,255,255,.10); padding-bottom:0; position:relative; }
    .tab { flex:1; display:flex; align-items:center; justify-content:center; gap:6px; padding:10px 4px;
      background:none; border:none; border-bottom:2px solid transparent; color:#eef1f7; opacity:.55;
      cursor:pointer; font-size:12.5px; transition:.15s; }
    .tab ha-icon { --mdc-icon-size:16px; }
    .tab:hover { opacity:.85; }
    .tab.active { opacity:1; border-bottom-color:var(--accent); color:#fff; }
    .panel { padding-top:14px; min-height:120px; }
    .sec-title { display:flex; align-items:center; gap:6px; font-size:12px; opacity:.75; margin-bottom:10px; font-weight:600; }
    .sec-title ha-icon { --mdc-icon-size:15px; color:var(--accent2); }
    .pool { margin-bottom:14px; }
    .pool-head { display:flex; justify-content:space-between; font-size:13px; margin-bottom:6px; }
    .pool-head b { font-weight:600; }
    .bar { height:9px; border-radius:99px; background:rgba(255,255,255,.10); overflow:hidden; }
    .bar i { display:block; height:100%; border-radius:99px; transition:width .5s ease; }
    .pool-meta { display:flex; gap:16px; font-size:10.5px; opacity:.6; margin-top:5px; }
    .chips { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:8px; }
    .chip { padding:9px 12px; border-radius:10px; background:rgba(255,255,255,.05); display:flex; flex-direction:column; gap:3px; }
    .chip span { font-size:10.5px; opacity:.6; }
    .chip b { font-size:14px; }
    .gpu-card { padding:12px 14px; border-radius:12px; background:rgba(255,255,255,.05);
      border:1px solid rgba(255,255,255,.08); margin-bottom:10px; }
    .gpu-head { display:flex; justify-content:space-between; align-items:center; font-size:13px; margin-bottom:8px; }
    .gpu-head b { display:inline-flex; align-items:center; gap:5px; font-weight:600; }
    .gpu-head b ha-icon { --mdc-icon-size:15px; color:var(--accent); }
    .gpu-head span { font-size:11.5px; opacity:.75; }
    .sub-title { font-size:11px; opacity:.6; margin:12px 0 6px; font-weight:600; }
    .events { display:flex; flex-direction:column; gap:6px; }
    .event-row { display:flex; align-items:center; gap:10px; padding:9px 11px; border-radius:10px;
      background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.07); }
    .event-row.is-new { border-left:3px solid var(--accent); background:rgba(124,140,255,.08); }
    .ev-dot { width:8px; height:8px; border-radius:50%; flex:none; }
    .ev-dot.ok { background:#5ee0c8; } .ev-dot.warn { background:#ffb74d; } .ev-dot.bad { background:#ff6b6b; }
    .ev-body { display:flex; flex-direction:column; min-width:0; flex:1; }
    .ev-body b { font-size:12.5px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ev-body span { font-size:10.5px; opacity:.6; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ev-meta { display:flex; flex-direction:column; align-items:flex-end; gap:2px; font-size:10px; opacity:.6; flex:none; }
    .list-tools { display:flex; gap:8px; align-items:center; margin-bottom:12px; }
    .search { flex:1; display:flex; align-items:center; gap:6px; padding:6px 10px; border-radius:9px;
      background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.10); }
    .search ha-icon { --mdc-icon-size:15px; opacity:.6; }
    .search input { flex:1; background:none; border:none; outline:none; color:#eef1f7; font-size:12.5px; min-width:0; }
    .search input::placeholder { color:rgba(238,241,247,.4); }
    .rows { display:flex; flex-direction:column; gap:6px; }
    .row { display:flex; align-items:center; gap:10px; padding:9px 12px; border-radius:11px;
      background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.07); }
    .dot { width:8px; height:8px; border-radius:50%; background:#5c6472; flex:none; }
    .dot.on { background:#26d0a0; box-shadow:0 0 8px rgba(38,208,160,.7); }
    .row-main { flex:1; min-width:0; display:flex; flex-direction:column; }
    .row-main b { font-size:12.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .row-main span { font-size:10px; opacity:.5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .row-metric { display:flex; flex-direction:column; align-items:flex-end; min-width:52px; }
    .row-metric span { font-size:9px; opacity:.5; }
    .row-metric b { font-size:12px; }
    .op { width:30px; height:30px; border-radius:8px; border:none; cursor:pointer; display:grid; place-items:center;
      background:rgba(255,255,255,.10); color:#eef1f7; flex:none; transition:.15s; padding:0; }
    .op ha-icon { --mdc-icon-size:16px; }
    .op.start { background:rgba(38,208,160,.22); color:#5ee8bd; }
    .op.stop { background:rgba(239,83,80,.18); color:#ff9a91; }
    .op:hover { filter:brightness(1.25); }
    .op.busy { opacity:.5; pointer-events:none; }
    .vm-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px; }
    .vm-card { padding:14px; border-radius:14px; background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08); }
    .vm-card.on { border-color:rgba(38,208,160,.3); }
    .vm-head { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
    .vm-ico { width:36px; height:36px; border-radius:10px; display:grid; place-items:center; flex:none;
      background:linear-gradient(135deg,var(--accent),var(--accent2)); }
    .vm-ico ha-icon { --mdc-icon-size:20px; color:#fff; }
    .vm-head div { flex:1; min-width:0; display:flex; flex-direction:column; }
    .vm-head b { font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .vm-head div span { font-size:10px; opacity:.5; }
    .status { font-size:10px; padding:3px 8px; border-radius:99px; background:rgba(255,255,255,.10); white-space:nowrap; }
    .status.on { background:rgba(38,208,160,.2); color:#5ee8bd; }
    .vm-metrics { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px; }
    .vm-metrics div { display:flex; flex-direction:column; }
    .vm-metrics span { font-size:9.5px; opacity:.5; }
    .vm-metrics b { font-size:12.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .vm-ops { display:flex; gap:7px; }
    .console-btn { flex:1; display:flex; align-items:center; justify-content:center; gap:5px; padding:7px 10px;
      border-radius:9px; border:1px solid rgba(255,255,255,.14); background:rgba(124,140,255,.18);
      color:#cfd6ff; cursor:pointer; font-size:11.5px; transition:.15s; }
    .console-btn:hover { background:rgba(124,140,255,.3); }
    .console-btn:disabled { opacity:.35; cursor:not-allowed; }
    .console-btn ha-icon { --mdc-icon-size:14px; }
    .pool-card { margin-bottom:10px; }
    details.disks { margin-top:8px; }
    details.disks summary { cursor:pointer; font-size:11.5px; opacity:.75; list-style:none; display:flex; align-items:center; gap:5px; }
    details.disks summary::-webkit-details-marker { display:none; }
    details.disks summary ha-icon { --mdc-icon-size:14px; }
    .disk-row { display:grid; grid-template-columns:.7fr 1.6fr .7fr .7fr .7fr; gap:6px; padding:7px 10px;
      font-size:10.5px; border-bottom:1px solid rgba(255,255,255,.06); align-items:center; }
    .disk-row .model { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .disk-row .health.ok { color:#5ee0c8; }
    .disk-row .health.bad { color:#ff8a80; }
    .none { text-align:center; padding:24px; opacity:.5; font-size:12px; }
    .foot { text-align:center; font-size:10px; opacity:.35; padding-top:14px; }
    .empty { display:flex; gap:12px; align-items:center; padding:24px; color:#eef1f7; }
    .empty ha-icon { --mdc-icon-size:28px; color:#ffb74d; }
    .empty p { margin:4px 0 0; opacity:.7; font-size:12px; }
    code { background:rgba(255,255,255,.1); padding:1px 5px; border-radius:4px; font-size:11px; }
    .vnc-modal { position:fixed; right:24px; bottom:24px; width:min(900px,92vw); height:min(640px,82vh);
      z-index:9999; background:#12161f; border:1px solid rgba(255,255,255,.16); border-radius:14px;
      overflow:hidden; box-shadow:0 20px 70px rgba(0,0,0,.6); display:flex; flex-direction:column; }
    .vnc-bar { display:flex; align-items:center; gap:8px; padding:9px 12px; background:#1c2230;
      border-bottom:1px solid rgba(255,255,255,.1); user-select:none; }
    .vnc-dot { width:8px; height:8px; border-radius:50%; background:#26d0a0; }
    .vnc-bar b { font-size:12px; }
    .vnc-spacer { flex:1; }
    .vnc-bar button { background:rgba(255,255,255,.08); border:none; color:#eef1f7; border-radius:7px;
      width:28px; height:26px; cursor:pointer; display:grid; place-items:center; padding:0; }
    .vnc-bar button ha-icon { --mdc-icon-size:15px; }
    .vnc-modal iframe { flex:1; border:none; width:100%; background:#000; }
  `;

  if (!customElements.get('zspace-card')) {
    customElements.define('zspace-card', ZspaceCard);
  }
  window.ZspaceCardVersion = VERSION;
})();
