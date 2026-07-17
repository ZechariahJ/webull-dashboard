"""Generate the research dashboard (an HTML file you open in a browser).

Two tabs, both limited to S&P 500 + NASDAQ-100 names:
  * Movers   — top gainers/losers/most-active (Webull screener) + market headlines
  * Earnings — upcoming earnings calendar (Finnhub); click a stock for its news

Per-stock news is fetched at build time and embedded, because the published page is
static and the browser must never see the API key.

    python3 report.py

This is a research aid you read *before deciding for yourself*. It is not advice
and places no orders.
"""
import html
import json
import logging
import sys
from datetime import datetime, timezone

from config import Config
from webull_client import WebullClient
import research
import news
import earnings as earnings_mod

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


def _news_items_html(items, empty_msg="No recent headlines for this stock."):
    """Render a list of headlines as <li> entries."""
    if not items:
        return f"<p class='muted pad'>{html.escape(empty_msg)}</p>"
    lis = []
    for n in items:
        meta = " · ".join(x for x in (html.escape(n.get("source", "")),
                                      html.escape(n.get("datetime", ""))) if x)
        url = html.escape(n.get("url", ""), quote=True)
        head = html.escape(n.get("headline", "(no title)"))
        link = f"<a href='{url}' target='_blank' rel='noopener'>{head}</a>" if url else head
        lis.append(f"<li><div class='hl'>{link}</div><div class='meta muted'>{meta}</div></li>")
    return f"<ul class='news'>{''.join(lis)}</ul>"


def _news_list(items):
    if not items:
        return ("<h2>Headlines</h2><p class='muted'>No news configured. Set "
                "NEWS_API_KEY (Finnhub free tier) to enable.</p>")
    return f"<h2>Headlines</h2>{_news_items_html(items)}"


def _earnings_table(rows):
    """Earnings calendar where each row expands to that stock's headlines."""
    if not rows:
        return ("<h2>Upcoming earnings</h2><p class='muted'>No earnings data. This tab "
                "needs NEWS_API_KEY (Finnhub free tier) set.</p>")
    trs = []
    for i, r in enumerate(rows):
        sym = html.escape(str(r.get("symbol", "")))
        est = r.get("eps_estimate")
        est = "—" if est in (None, "") else html.escape(f"{float(est):.2f}") \
            if isinstance(est, (int, float)) else html.escape(str(est))
        trs.append(
            f"<tr class='er-row' data-target='er{i}' tabindex='0'>"
            f"<td class='sym'>{sym} <span class='chev'>›</span></td>"
            f"<td>{html.escape(str(r.get('date', '')))}</td>"
            f"<td>{html.escape(str(r.get('session') or '—'))}</td>"
            f"<td class='num'>{est}</td></tr>"
            f"<tr class='er-news' id='er{i}' hidden><td colspan='4'>"
            f"<div class='er-news-box'><div class='er-news-hd muted'>Recent news — {sym}</div>"
            f"{_news_items_html(r.get('news') or [])}</div></td></tr>")
    return ("<h2>Upcoming earnings</h2>"
            "<p class='muted hint'>Click any stock to see its recent news.</p>"
            "<table class='earnings'><thead><tr><th>Symbol</th><th>Date</th>"
            "<th>Session</th><th>EPS est.</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>")


def render_html(data: dict, news_items=None, earnings_rows=None) -> str:
    """Pure renderer: movers data + market news + earnings rows -> HTML string."""
    news_items = news_items or []
    earnings_rows = earnings_rows or []
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
  /* tabs */
  .tabs {{ display: flex; gap: 4px; border-bottom: 1px solid #2a2e35; margin: 20px 0 4px; }}
  @media (prefers-color-scheme: light) {{ .tabs {{ border-color:#e2e5ea; }} }}
  .tab {{ appearance: none; background: none; border: 0; color: inherit; cursor: pointer;
          font: inherit; font-weight: 600; font-size: 14px; padding: 8px 14px; opacity: .55;
          border-bottom: 2px solid transparent; margin-bottom: -1px; }}
  .tab:hover {{ opacity: .85; }}
  .tab[aria-selected="true"] {{ opacity: 1; border-bottom-color: #3b82f6; }}
  .panel[hidden] {{ display: none; }}
  /* earnings */
  .hint {{ font-size: 12px; margin: 0 0 8px; }}
  table.earnings tr.er-row {{ cursor: pointer; }}
  table.earnings tr.er-row:hover td {{ background: rgba(127,127,127,.08); }}
  table.earnings tr.er-row:focus {{ outline: 2px solid #3b82f6; outline-offset: -2px; }}
  .chev {{ display: inline-block; opacity: .45; font-weight: 400; transition: transform .15s; }}
  tr.er-row.open .chev {{ transform: rotate(90deg); }}
  .er-news-box {{ padding: 4px 4px 12px 4px; }}
  .er-news-hd {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
                 margin-bottom: 4px; }}
  .er-news-box ul.news li {{ border-bottom: 0; padding: 5px 0; }}
  .pad {{ padding: 6px 0 10px; font-size: 13px; }}
</style></head>
<body><div class="wrap">
<header>
  <h1>Market Dashboard</h1>
  <div class="muted">Generated {gen} UTC</div>
  <div class="disclaimer">Informational only — public market data, not investment
  advice. Limited to S&amp;P 500 + NASDAQ-100 names. Do your own research
  before trading.</div>
</header>

<div class="tabs" role="tablist">
  <button class="tab" role="tab" id="tab-movers" aria-controls="panel-movers"
          aria-selected="true">Movers</button>
  <button class="tab" role="tab" id="tab-earnings" aria-controls="panel-earnings"
          aria-selected="false">Earnings</button>
</div>

<section class="panel" id="panel-movers" role="tabpanel" aria-labelledby="tab-movers">
<div class="cols">
  <div>{_movers_table("Top gainers (1d)", data.get("gainers", []))}</div>
  <div>{_movers_table("Top losers (1d)", data.get("losers", []))}</div>
</div>
{_movers_table("Most active", data.get("most_active", []))}
{_news_list(news_items)}
</section>

<section class="panel" id="panel-earnings" role="tabpanel" aria-labelledby="tab-earnings" hidden>
{_earnings_table(earnings_rows)}
</section>
{err_html}
</div>
<script>
(function () {{
  // --- tabs ---
  var tabs = [].slice.call(document.querySelectorAll('.tab'));
  function select(tab) {{
    tabs.forEach(function (t) {{
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    }});
    // Remember the tab across refreshes (the page reloads every morning).
    try {{ localStorage.setItem('dashTab', tab.id); }} catch (e) {{}}
  }}
  tabs.forEach(function (t) {{ t.addEventListener('click', function () {{ select(t); }}); }});
  try {{
    var saved = document.getElementById(localStorage.getItem('dashTab'));
    if (saved) select(saved);
  }} catch (e) {{}}

  // --- earnings rows expand to that stock's news ---
  function toggle(row) {{
    var box = document.getElementById(row.getAttribute('data-target'));
    if (!box) return;
    box.hidden = !box.hidden;
    row.classList.toggle('open', !box.hidden);
  }}
  [].slice.call(document.querySelectorAll('tr.er-row')).forEach(function (row) {{
    row.addEventListener('click', function () {{ toggle(row); }});
    row.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); toggle(row); }}
    }});
  }});
}})();
</script>
</body></html>"""


def main():
    Config.validate()
    wb = WebullClient(Config)
    data = research.gather(wb, Config)
    news_items = news.fetch_market_news(Config.NEWS_API_KEY)

    # Earnings tab (Finnhub). Never let it sink the whole report.
    earnings_rows = []
    try:
        earnings_rows = earnings_mod.gather(
            Config.NEWS_API_KEY, research.load_universe(),
            days=Config.EARNINGS_DAYS, max_rows=Config.EARNINGS_MAX,
            news_per=Config.EARNINGS_NEWS_PER)
    except Exception as e:  # noqa: BLE001 - report-level resilience
        log.warning("earnings tab failed: %s", e)
        data.setdefault("errors", []).append(f"earnings: {e}")

    out = render_html(data, news_items, earnings_rows)
    with open(Config.REPORT_OUTPUT, "w", encoding="utf-8") as f:
        f.write(out)
    log.info("Wrote dashboard -> %s (%d gainers, %d losers, %d active, "
             "%d headlines, %d earnings)",
             Config.REPORT_OUTPUT, len(data.get("gainers", [])),
             len(data.get("losers", [])), len(data.get("most_active", [])),
             len(news_items), len(earnings_rows))
    if data.get("errors"):
        log.warning("Some sections failed; see the dashboard's error box.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
