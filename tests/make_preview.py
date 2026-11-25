"""生成 zspace-card 的本地预览页与截图（无需 HA）。

用法：
    python tests/make_preview.py        # 生成 preview_*.html
    # 再用 chrome headless 截图，见文件末尾说明

预览用 tests/fixtures/view_sample.json 作数据（由 aggregate.build_view 生成），
在浏览器里 mock 了 ha-card / ha-icon，可直接验证布局与交互。
"""

from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures"
ROOT = HERE.parent

# 图标名 → 可见符号，让预览里能看清图标占位（真实 HA 会渲染 mdi 图标）
ICON_MAP = """
const GM={cpu:'▣',memory:'▤',thermometer:'◉',fan:'✳',network:'⌘',download:'↓',
 docker:'▦',monitor:'▢',harddisk:'▭',database:'◫',play:'▶',stop:'■',restart:'↻',
 console:'⌨',check:'✓',alert:'!',close:'✕',fullscreen:'⛶',chevron:'⌄',power:'⏻',
 refresh:'↻',nas:'▥',palette:'◐',magnify:'⌕','arrow-down':'↓','arrow-up':'↑',
 'information-outline':'i','chevron-down':'⌄',
 'expansion-card':'▤',brain:'◈','backup-restore':'↺','bell-outline':'♢',speaker:'▷'};
class HaIcon extends HTMLElement{connectedCallback(){
 const n=(this.getAttribute('icon')||'').split(':')[1]||'';
 this.textContent=GM[n]||'•';this.style.cssText='display:inline-grid;place-items:center;font-style:normal';}}
"""

TEMPLATE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>预览 · __TABNAME__</title>
<style>body{margin:0;background:linear-gradient(135deg,#0d1020,#141a2e 50%,#0d1a1e);
 min-height:100vh;padding:24px;box-sizing:border-box}.wrap{max-width:720px;margin:0 auto}
 .hint{color:#8a95b0;font-size:12px;margin-bottom:10px;text-align:center}</style></head><body>
<div class="wrap"><div class="hint">极空间控制面板 · __TABNAME__</div>
<zspace-card id="card"></zspace-card></div>
<script>
class HaCard extends HTMLElement{connectedCallback(){this.style.display='block'}}
customElements.define('ha-card',HaCard);
__ICONMAP__
customElements.define('ha-icon',HaIcon);
__CARDJS__
</script><script>
const VIEW=__VIEW__;
const card=document.getElementById('card');
card.setConfig({entity:'sensor.zspace_all_entities',name:'极空间 Z4Pro',
 theme:'aurora',default_tab:'__TAB__'});
card.hass={states:{'sensor.zspace_all_entities':{state:'42',attributes:{view:VIEW}}},
 callService:(d,s,o)=>console.log('service',d,s,o)};
</script></body></html>"""


def main() -> None:
    payload = json.loads((FIX / "view_sample.json").read_text(encoding="utf-8"))
    payload["view"]["overview"]["disks_bad"] = 1
    view_json = json.dumps(payload["view"], ensure_ascii=False)
    card_js = (ROOT / "www" / "zspace-card.js").read_text(encoding="utf-8")

    for tab, label in (
        ("overview", "总览"),
        ("containers", "容器"),
        ("vms", "虚拟机"),
        ("storage", "存储"),
        ("ops", "运维"),
    ):
        html = (
            TEMPLATE.replace("__ICONMAP__", ICON_MAP)
            .replace("__CARDJS__", card_js)
            .replace("__VIEW__", view_json)
            .replace("__TAB__", tab)
            .replace("__TABNAME__", label)
        )
        (FIX / f"preview_{tab}.html").write_text(html, encoding="utf-8")
        print("generated", f"preview_{tab}.html")

    print("\n截图命令示例（Chrome headless）：")
    print('  chrome --headless --no-sandbox --window-size=760,1000 \\')
    print('    --screenshot=shot_containers.png \\')
    print('    file:///.../tests/fixtures/preview_containers.html')


if __name__ == "__main__":
    main()
