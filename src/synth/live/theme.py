"""Langfuse design tokens + a styled page shell — shared across all playground front-ends.

Tokens extracted from the Langfuse styleguide (verified against the deployed stylesheets):
a warm-paper light theme, the signature lime CTA accent (#fbff81), a *flat* system (no
shadows — depth comes from borders, dashed dividers and the diagonal stripe motif), and the
Inter / Space Grotesk (F37 Analog stand-in) / Geist Mono type stack.

``page(body, title=…)`` wraps content in the styled shell (fonts, tokens, the processing
overlay). Reuse it for any new UI so they all share one visual language.
"""
from __future__ import annotations

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&'
    'family=Space+Grotesk:wght@500;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

CSS = """
:root{
  --surface-bg:#f6f6f3; --surface-1:#edede8; --surface-2:#e5e5e1; --surface-beige:#f1ede1;
  --cta-primary:#fbff81; --active-tint:#fbffd6;
  --text-primary:#222220; --text-secondary:#3d3d38; --text-tertiary:#6b6b66; --text-disabled:#a7a7a0;
  --text-links:#4f39f6; --success:#538a2e; --error:#cc3314;
  --line-structure:#cfcfc9; --line-divider-dash:#bebeb6; --line-cta:#404039;
  --radius:.5rem; --radius-sm:calc(var(--radius) - 4px);
  --stripe-period:6px; --stripe-line:#6c67601a;
  --font-sans:"Inter",ui-sans-serif,system-ui,sans-serif;
  --font-display:"Space Grotesk","Inter",sans-serif;
  --font-mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--surface-1);color:var(--text-primary);font-family:var(--font-sans);
  line-height:1.55;-webkit-font-smoothing:antialiased;padding:52px 20px 80px}
.wrap{max-width:560px;margin:0 auto}
.eyebrow{font-family:var(--font-mono);font-size:11px;text-transform:uppercase;letter-spacing:.14em;
  color:var(--text-tertiary);margin-bottom:14px}
h1{font-family:var(--font-display);font-weight:700;font-size:34px;letter-spacing:-.02em;line-height:1.05}
.mark{background:var(--cta-primary);color:var(--text-primary);padding:0 .1em;border-radius:3px;
  box-decoration-break:clone;-webkit-box-decoration-break:clone}
.sub{color:var(--text-tertiary);font-size:14px;margin:10px 0 26px}
label{display:block;font-family:var(--font-mono);font-size:11px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--text-tertiary);margin:16px 0 6px}
select,input,textarea{width:100%;padding:10px 12px;border:1px solid var(--line-structure);
  border-radius:var(--radius);background:var(--surface-bg);color:var(--text-primary);font:15px/1.4 var(--font-sans)}
select:focus,input:focus,textarea:focus{outline:none;border-color:var(--line-cta)}
.line input{border-color:var(--line-cta)} textarea{resize:vertical}
.note{font-size:12px;color:var(--text-tertiary);margin-top:8px}
button{margin-top:22px;width:100%;padding:12px;border:1px solid var(--line-cta);border-radius:var(--radius);
  background:var(--cta-primary);color:#222220;font:600 15px var(--font-sans);cursor:pointer}
button:hover{filter:brightness(.97)}
.ghost button{background:transparent;color:var(--text-primary)}
.ghost button:hover{background:#403d391a;filter:none}
.card{margin:22px 0;padding:20px;border:1px solid var(--line-structure);border-radius:var(--radius);
  background:var(--surface-bg)}
.card.active{background:var(--active-tint);border-top:3px solid var(--cta-primary)}
.verdict{font-family:var(--font-display);font-size:30px;font-weight:700;letter-spacing:-.01em}
.approve{color:var(--success)} .reject{color:var(--error)}
.pill{font-family:var(--font-mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  padding:4px 10px;border-radius:999px;background:var(--text-primary);color:var(--surface-bg);
  margin-left:10px;vertical-align:3px}
.kv{display:flex;justify-content:space-between;gap:14px;padding:9px 0;border-bottom:1px dashed var(--line-divider-dash);
  font-size:14px} .kv:last-child{border-bottom:0} .kv span:first-child{color:var(--text-tertiary)}
.kv span:last-child{text-align:right}
h2{font-family:var(--font-display);font-weight:700;font-size:22px;letter-spacing:-.01em;margin-bottom:6px}
a{color:var(--text-links);text-decoration:none} a:hover{text-decoration:underline}
.back{display:inline-block;margin-top:6px;font-family:var(--font-mono);font-size:12px;color:var(--text-tertiary)}
.wrap.wide{max-width:980px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}
@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}}
.kpi{padding:16px;border:1px solid var(--line-structure);border-radius:var(--radius);background:var(--surface-bg)}
.kpi .klabel{font-family:var(--font-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--text-tertiary)}
.kpi .kvalue{font-family:var(--font-display);font-size:26px;font-weight:700;letter-spacing:-.01em;margin:4px 0 2px}
.kpi .kdelta{font-family:var(--font-mono);font-size:11px}
.kdelta.bad{color:var(--error)} .kdelta.good{color:var(--success)} .kdelta.flat{color:var(--text-tertiary)}
.chip{display:inline-block;font-family:var(--font-mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;
  padding:3px 9px;border-radius:999px;border:1px solid var(--line-structure)}
.chip.green{background:#538a2e14;color:var(--success);border-color:#538a2e55}
.chip.red{background:#cc331410;color:var(--error);border-color:#cc331455}
.charts{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:4px 0 18px}
@media(max-width:760px){.charts{grid-template-columns:1fr}}
.chart{padding:14px 16px;border:1px solid var(--line-structure);border-radius:var(--radius);background:var(--surface-bg)}
.chart .klabel{font-family:var(--font-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--text-tertiary);margin-bottom:8px}
.chart svg{display:block;width:100%;height:auto}
.memo{padding:16px 18px;border:1px solid var(--line-structure);border-left:3px solid var(--cta-primary);
  border-radius:var(--radius);background:var(--surface-beige);font-size:14px}
.memo .quote{font-style:italic;color:var(--text-secondary)}
.overlay{display:none;position:fixed;inset:0;z-index:50;align-items:center;justify-content:center;flex-direction:column;
  background:repeating-linear-gradient(-45deg,var(--stripe-line) 0,var(--stripe-line) 1px,transparent 1px,transparent var(--stripe-period)),var(--surface-1)}
.spinner{width:42px;height:42px;border:3px solid var(--line-structure);border-top-color:var(--text-primary);
  border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.overlay p{font-family:var(--font-mono);font-size:11px;text-transform:uppercase;letter-spacing:.12em;
  color:var(--text-tertiary);margin-top:16px}
"""


def page(body: str, *, title: str = "Langfuse", wide: bool = False) -> str:
    """Wrap ``body`` in the Langfuse-styled HTML shell (fonts, tokens, processing overlay).
    ``wide`` switches to the 980px dashboard layout."""
    wrap = "wrap wide" if wide else "wrap"
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>{FONT_LINKS}<style>{CSS}</style></head>"
        f"<body><div class='{wrap}'>{body}</div>"
        f"<div class='overlay' id='overlay'><div class='spinner'></div><p id='ovmsg'>Processing decision</p></div>"
        f"<script>document.addEventListener('submit',function(e){{var a=e.target.getAttribute('action')||'';"
        f"document.getElementById('ovmsg').textContent=a.endsWith('/dispute')?'Logging dispute':'Processing decision';"
        f"document.getElementById('overlay').style.display='flex';}});</script>"
        f"</body></html>")
