"""Page skeleton shared by the offline browser reports: tokens, base CSS, tab and tooltip primitives.

No network reference, no build step: the caller inlines its data as JSON and supplies the script that
renders it. The shell knows nothing about a benchmark board or the eval dashboard.
"""
import html

CSS = r""":root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
--blue:#2a78d6;--blue100:#cde2fb;--blue250:#86b6ef;--blue400:#3987e5;--blue550:#1c5cab;--blue700:#0d366b;--orange:#eb6834;--aqua:#1baf7a;--violet:#4a3aa7}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--blue:#3987e5;--blue100:#184f95;--blue250:#1c5cab;--blue400:#3987e5;--blue550:#6da7ec;--blue700:#9ec5f4;--orange:#d95926;--aqua:#199e70;--violet:#9085e9}}
*{box-sizing:border-box}body{margin:0;background:var(--plane);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:20px 28px 8px}h1{font-size:22px;margin:0 0 4px}header p{margin:0;color:var(--ink2)}
.kpis{display:flex;gap:12px;padding:12px 28px;flex-wrap:wrap}.kpi{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;min-width:150px}
.kpi .l{font-size:12px;color:var(--muted)}.kpi .v{font-size:26px;font-weight:600;line-height:1.1}.kpi .s{font-size:12px;color:var(--ink2)}
nav{display:flex;gap:4px;padding:6px 28px;flex-wrap:wrap;border-bottom:1px solid var(--grid)}nav button{background:none;border:1px solid transparent;border-radius:6px;padding:6px 10px;color:var(--ink2);cursor:pointer;font:inherit}
nav button.on{background:var(--surface);border-color:var(--border);color:var(--ink);font-weight:600}
main{padding:16px 28px 40px}.panel{display:none}.panel.on{display:block}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.card h2{margin:0 0 4px;font-size:17px}.card .sub{color:var(--ink2);margin:0 0 10px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}.chip{border:1px solid var(--border);border-radius:999px;padding:3px 10px;cursor:pointer;color:var(--ink2);background:none;font:inherit;font-size:12.5px}
.chip.on{background:var(--blue100);color:var(--ink);border-color:var(--blue250)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--muted);font-weight:500;cursor:pointer;white-space:nowrap;user-select:none}th.num,td.num{text-align:right}tr:hover td{background:rgba(42,120,214,.06)}
svg text{font:12px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--ink2)}svg .axis{stroke:var(--axis)}svg .grid{stroke:var(--grid)}svg .lab{fill:var(--ink)}svg .muted{fill:var(--muted)}
.tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:12.5px;max-width:420px;box-shadow:0 4px 18px rgba(0,0,0,.12);display:none;z-index:9}
.tip b{display:block;margin-bottom:2px}.tip .q{color:var(--ink2);font-style:italic}
.legend{display:flex;gap:16px;font-size:12.5px;color:var(--ink2);margin:6px 0 0}.sw{display:inline-block;width:14px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:5px;background:var(--blue)}
.note{font-size:12.5px;color:var(--ink2)}
"""

JS = {
    "fk": r"""const fk=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?Math.round(n/1e3)+'k':''+n;""",
    "esc": r"""const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));""",
    "tip": r"""const tip=document.getElementById('tip');
function showTip(e,html){tip.innerHTML=html;tip.style.display='block';moveTip(e)}function moveTip(e){tip.style.left=Math.min(e.clientX+14,innerWidth-440)+'px';tip.style.top=(e.clientY+14)+'px'}function hideTip(){tip.style.display='none'}""",
    "tabs": r"""const nav=document.getElementById('nav'),main=document.getElementById('main');const panels={};
function addTab(id,label){const b=document.createElement('button');b.textContent=label;b.onclick=()=>show(id);b.dataset.id=id;nav.appendChild(b);const p=document.createElement('section');p.className='panel';p.id='p-'+id;main.appendChild(p);panels[id]=p;return p}
function show(id){for(const b of nav.children)b.classList.toggle('on',b.dataset.id===id);for(const k in panels)panels[k].classList.toggle('on',k===id);location.hash=id}""",
}


def marker(name):
    return "/*shell:%s*/" % name


def _compose(script):
    """A primitive whose marker sits in the caller's script lands there; the rest are prepended."""
    prefix = []
    for name, snippet in JS.items():
        if marker(name) in script:
            script = script.replace(marker(name), snippet)
        else:
            prefix.append(snippet)
    return "\n".join(prefix + [script])


def page(title, *, css_extra="", header="", data, script):
    """One self-contained document. `data` is a JSON string, inlined with `</` escaped."""
    safe = data.replace("</", "<\\/")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>%s</title>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<style>\n%s%s</style></head><body>\n"
        "<header>%s</header>\n"
        '<div class="kpis" id="kpis"></div>\n<nav id="nav"></nav>\n<main id="main"></main>\n'
        '<div class="tip" id="tip"></div>\n'
        '<script id="data" type="application/json">%s</script>\n'
        "<script>\n%s\n</script></body></html>\n"
    ) % (html.escape(title, quote=False), CSS, css_extra, header, safe, _compose(script))
