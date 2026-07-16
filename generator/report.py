"""Generate the research dashboard (an HTML file you open in a browser).

Pulls market movers + upcoming earnings from Webull and (optionally) headlines
from a free news API, then writes a self-contained HTML page. Run it daily on a
schedule (see README) and open the file, or just run it on demand.

    python3 report.py

This is a research aid you read *before deciding for yourself*. It is not advice
and places no orders.
"""
import html
import logging
import sys
from datetime import datetime, timezone

from config import Config
from webull_client import WebullClient
import research
import news

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("report")


def _fmt_pct(x):
    # change_ratio may be a fraction (0.05) or a percent (5.0); normalize to %.
    v = x * 100 if -1.5 < x < 1.5 else x
    return f"{v:+.2f}%"


def _movers_table(title, rows):
    if not rows:
        return f"<h2>{html.escape(title)}</h2><p class='muted'>No data.</p>"
    trs = []
    for r in rows:
        cls = "up" if r["change_ratio"] >= 0 else "down"
        trs.append(
            f"<tr><td class='sym'>{html.escape(str(r['symbol']))}</td>"
            f"<td class='name'>{html.escape(str(r['name']))}</td>"
            f"<td class='num'>{r['price']:.2f}</td>"
            f"<td class='num {cls}'>{_fmt_pct(r['change_ratio'])}</td>"
            f"<td class='num'>{int(r['volume']):,}</td></tr>")
    return (f"<h2>{html.escape(title)}</h2><table>"
            "<thead><tr><th>Symbol</th><th>Name</th><th>Price</th>"
            "<th>Change</th><th>Volume</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>")


def _news_list(items):
    if not items:
        return ("<h2>Headlines</h2><p class='muted'>No news configured. Set "
                "NEWS_API_KEY (Finnhub free tier) in .env to enable.</p>")
    lis = []
    for n in items:
        meta = " · ".join(x for x in (html.escape(n.get("source", "")),
                                      html.escape(n.get("datetime", ""))) if x)
        url = html.escape(n.get("url", ""), quote=True)
        head = html.escape(n.get("headline", "(no title)"))
        link = f"<a href='{url}' target='_blank' rel='noopener'>{head}</a>" if url else head
        lis.append(f"<li><div class='hl'>{link}</div><div class='meta muted'>{meta}</div></li>")
    return f"<h2>Headlines</h2><ul class='news'>{''.join(lis)}</ul>"


def render_html(data: dict, news_items=None) -> str:
    """Pure renderer: data dict (from research.gather) + news list -> HTML string."""
    news_items = news_items or []
    gen = html.escape(data.get("generated_at", ""))
    errors = data.get("errors", [])
    err_html = ""
    if errors:
        items = "".join(f"<li>{html.escape(str(e))}</li>" for e in errors)
        err_html = f"<div class='errors'><strong>Some sections failed:</strong><ul>{items}</ul></div>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market Dashboard</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0;
         background: #0f1115; color: #e6e8eb; }}
  @media (prefers-color-scheme: light) {{ body {{ background:#f6f7f9; color:#1a1d21; }} }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 24px 20px 64px; }}
  header h1 {{ margin: 0 0 4px; font-size: 22px; }}
  .muted {{ opacity: .6; }}
  .disclaimer {{ font-size: 12px; opacity:.65; margin: 8px 0 24px;
                 border-left: 3px solid #f5a623; padding: 4px 10px; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .04em;
        margin: 28px 0 8px; opacity: .8; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #2a2e35; }}
  @media (prefers-color-scheme: light) {{ th, td {{ border-color:#e2e5ea; }} }}
  th {{ font-size: 12px; opacity: .6; font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.sym {{ font-weight: 700; }}
  td.name {{ opacity: .7; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .up {{ color: #3fb950; }} .down {{ color: #f85149; }}
  ul.news {{ list-style: none; padding: 0; margin: 0; }}
  ul.news li {{ padding: 8px 0; border-bottom: 1px solid #2a2e35; }}
  ul.news a {{ color: inherit; text-decoration: none; }}
  ul.news a:hover {{ text-decoration: underline; }}
  .meta {{ font-size: 12px; }}
  .errors {{ background: rgba(248,81,73,.1); border:1px solid rgba(248,81,73,.4);
             padding: 8px 12px; border-radius: 8px; font-size: 13px; margin-top: 16px; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 32px; }}
  @media (max-width: 640px) {{ .cols {{ grid-template-columns: 1fr; }} }}
</style></head>
<body><div class="wrap">
<header>
  <h1>Market Dashboard</h1>
  <div class="muted">Generated {gen} UTC</div>
  <div class="disclaimer">Informational only — public market data, not investment
  advice. Movers are limited to S&amp;P 500 + NASDAQ-100 names. Do your own research
  before trading.</div>
</header>
<div class="cols">
  <div>{_movers_table("Top gainers (1d)", data.get("gainers", []))}</div>
  <div>{_movers_table("Top losers (1d)", data.get("losers", []))}</div>
</div>
{_movers_table("Most active", data.get("most_active", []))}
{_news_list(news_items)}
{err_html}
</div></body></html>"""


def main():
    Config.validate()
    wb = WebullClient(Config)
    data = research.gather(wb, Config)
    news_items = news.fetch_market_news(Config.NEWS_API_KEY)
    out = render_html(data, news_items)
    with open(Config.REPORT_OUTPUT, "w", encoding="utf-8") as f:
        f.write(out)
    log.info("Wrote dashboard -> %s (%d gainers, %d losers, %d active, %d headlines)",
             Config.REPORT_OUTPUT, len(data.get("gainers", [])),
             len(data.get("losers", [])), len(data.get("most_active", [])),
             len(news_items))
    if data.get("errors"):
        log.warning("Some sections failed; see the dashboard's error box.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
