"""生成侧边栏面板模式的本地预览截图（无需 HA）。

用法：
    python tests/make_panel_preview.py
    # 再用 chrome headless 截图 panel_preview.html，见 README
"""

from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures"
ROOT = HERE.parent

# 图标名 → 可见符号（真实 HA 会渲染 mdi 图标）
ICON_MAP = """
const GM={cpu:'▣',memory:'▤',thermometer:'◉',fan:'✳',network:'⌘',download:'↓',
 docker:'▦',monitor:'▢',harddisk:'▭',database:'◫',play:'▶',stop:'■',restart:'↻',
 console:'⌨',check:'✓',alert:'!',close:'✕',fullscreen:'⛶',chevron:'⌄',power:'⏻',
 refresh:'↻',nas:'▥',palette:'◐',magnify:'⌕','arrow-down':'↓','arrow-up':'↑',
 'information-outline':'i','chevron-down':'⌄'};
class HaIcon extends HTMLElement{connectedCallback(){
 const n=(this.getAttribute('icon')||'').split(':')[1]||'';
 this.textContent=GM[n]||'•';
 this.style.cssText='display:inline-grid;place-items:center;font-style:normal';}}
"""

HEAD = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>面板模式预览</title>
<style>body{margin:0;background:#0a0d18;
 font-family:system-ui,-apple-system,'Microsoft YaHei',sans-serif}</style></head><body>
<div style="display:flex;height:32px;background:#1c2230;color:#8a95b0;font-size:12px;
 align-items:center;padding:0 16px;gap:14px"><span>≡ 极空间</span><span>▤ 概览</span>
 <span style="color:#fff">▥ 极空间（侧边栏面板）</span><span style="margin-left:auto">⚙</span></div>
<ha-panel-zspace id="panel"></ha-panel-zspace>
<script>
class HaCard extends HTMLElement{connectedCallback(){this.style.display='block'}}
customElements.define('ha-card',HaCard);
"""

TAIL = """
customElements.define('ha-icon',HaIcon);
__PANEL_JS__
</script><script>
const VIEW=__VIEW__;
const panel=document.getElementById('panel');
panel.panelConfig={};
panel.hass={states:{'sensor.zspace_all_entities':{state:'42',attributes:{view:VIEW}}},
            callService:()=>{}};
</script></body></html>"""


def main() -> None:
    view = json.loads((FIX / "view_sample.json").read_text(encoding="utf-8"))["view"]
    view["overview"]["disks_bad"] = 1  # 演示异常盘徽章
    js = (ROOT / "custom_components/zspace_nas/panel.js").read_text(encoding="utf-8")
    html = (HEAD + ICON_MAP + TAIL).replace("__PANEL_JS__", js).replace(
        "__VIEW__", json.dumps(view, ensure_ascii=False)
    )
    (FIX / "panel_preview.html").write_text(html, encoding="utf-8")
    print(f"panel_preview.html 生成：{len(html)} 字节")


if __name__ == "__main__":
    main()
