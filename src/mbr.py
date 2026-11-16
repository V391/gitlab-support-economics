"""Render the one-page Monthly Business Review (self-contained HTML, charts inlined)."""
import html

import pandas as pd

TODAY = "Today's AI footprint"


def money(v):
    return f"${v / 1e6:,.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}k"


def pct(v, d=0):
    return f"{v * 100:.{d}f}%"


def table(df, cols, fmt):
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols.values())
    body = ""
    for _, r in df.iterrows():
        body += "<tr>" + "".join(f"<td>{fmt.get(c, str)(r[c])}</td>" for c in cols) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(s, imgs, plan, at_risk, alerts, qual, mbr, scorecard):
    last = mbr.iloc[-1]
    prev = mbr.iloc[-2]
    ss, aa, ch = s["self_service"], s["agent_assist"], s["churn"]
    caps = plan.groupby("scenario")[["fte_needed", "fy28_cost"]].sum()
    tiles = [
        ("Tickets, Oct 2026", f"{int(last.tickets):,}", f"{(last.tickets / prev.tickets - 1) * 100:+.0f}% vs Sep"),
        ("Cost per ticket", f"${last.cost_per_ticket:,.0f}", f"12 mo spend {money(s['last12_support_cost'])}"),
        ("First reply SLA", pct(last.sla_attainment, 1), "target 95%"),
        ("SSAT", pct(last.ssat, 1), "target 95%"),
        ("AI self-service", f"{-ss['howto_config_tickets']['pct'] * 100:.0f}%", "how-to + config tickets avoided, vs holdout"),
        ("Agent assist", f"{-aa['effect_when_used'] * 100:.0f}%", f"handle time when used (naive read: {-aa['naive_used_vs_not'] * 100:.0f}%)"),
        ("ARR flagged at risk", money(ch["arr_flagged"]), f"{ch['flagged']} accounts renewing by Feb 2027"),
        ("FY28 headcount", f"{caps.loc[TODAY, 'fte_needed']:.0f}",
         f"{s['current_fte']} today; {caps.loc['No AI', 'fte_needed']:.0f} without AI"),
    ]
    tile_html = "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div><div class='n'>{n}</div></div>"
                        for k, v, n in tiles)
    risk = table(at_risk.head(12), {"account_name": "Account", "plan": "Plan", "deployment": "Deployment", "arr": "ARR",
                                    "next_renewal": "Renews", "churn_risk": "Risk", "drivers": "Why"},
                 {"arr": money, "churn_risk": lambda v: pct(v), "next_renewal": lambda v: pd.Timestamp(v).strftime("%b %Y")})
    cap = table(plan.round(1), {"region": "Region", "scenario": "Scenario", "current_fte": "Today", "fte_needed": "FY28 FTE",
                                "fy28_tickets": "FY28 tickets", "fy28_cost": "FY28 cost"},
                {"fte_needed": lambda v: f"{v:.1f}", "fy28_tickets": lambda v: f"{v:,.0f}", "fy28_cost": money})
    al = alerts[alerts.week >= pd.Timestamp("2026-04-01")]
    alert_html = table(al, {"region": "Region", "week": "Week of", "alert": "Alert", "detail": "Detail"},
                       {"week": lambda v: pd.Timestamp(v).strftime("%d %b %Y")}) if len(al) else "<p>No alerts.</p>"
    qtab = table(qual.head(8), {"product_stage": "Stage", "tickets": "Tickets", "quality_cost": "Escalated ticket cost",
                                "quality_cost_share": "Share of stage cost", "real_customer_issues": "Real customer issues",
                                "real_median_days_to_close": "Median days to close (real)"},
                 {"quality_cost": money, "quality_cost_share": pct, "tickets": lambda v: f"{int(v):,}",
                  "real_customer_issues": lambda v: f"{int(v):,}" if pd.notna(v) else "",
                  "real_median_days_to_close": lambda v: f"{v:.0f}" if pd.notna(v) else ""})
    sc = scorecard.groupby("region").agg(engineers=("agent_id", "nunique"), tickets=("tickets", "sum"),
                                         utilization=("utilization", "mean"), sla=("sla", "mean"),
                                         ssat=("ssat", "mean"), ai_rate=("ai_rate", "mean")).reset_index()
    sct = table(sc, {"region": "Region", "engineers": "Engineers", "tickets": "Tickets", "utilization": "Utilization",
                     "sla": "SLA met", "ssat": "SSAT", "ai_rate": "AI assist use"},
                {"utilization": pct, "sla": lambda v: pct(v, 1), "ssat": lambda v: pct(v, 1), "ai_rate": pct})
    img = lambda k: f"<img src='data:image/png;base64,{imgs[k]}'/>"  # noqa: E731
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Support MBR October 2026</title>
<style>
:root{{--ink:#1f1e24;--muted:#6b6a72;--line:#e6e4ea;--accent:#E24329;--bg:#fff;--card:#faf9fb}}
body{{font-family:-apple-system,Segoe UI,Inter,Arial,sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;}}
main{{max-width:1080px;margin:0 auto}} h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:28px 0 8px;border-bottom:2px solid var(--accent);padding-bottom:4px}}
.sub{{color:var(--muted);font-size:13px;margin:4px 0 16px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}}
.k{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}} .v{{font-size:22px;font-weight:650;margin:2px 0}} .n{{font-size:12px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}} img{{max-width:100%}}
table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left}} th{{color:var(--muted);font-weight:600}}
.wrap{{overflow-x:auto}} ul{{font-size:13px;line-height:1.5;padding-left:18px}}
</style></head><body><main>
<h1>Support Engineering MBR, October 2026</h1>
<div class='sub'>Demo data: Zendesk-shaped tickets simulated for 1,500 accounts, product stage mix and bug share calibrated to {s.get('real_issue_count', 'public')} real gitlab-org customer issues. Built from SQL marts in DuckDB.</div>
<div class='tiles'>{tile_html}</div>
<h2>Headlines</h2>
<ul>
<li><b>October volume rose {(last.tickets / prev.tickets - 1) * 100:.0f}% on September and first reply SLA fell to {pct(last.sla_attainment, 1)}</b>: the seasonal October peak pushed AMER and EMEA past the utilization ceiling. The same pattern hit January and March; FY28 staffing is sized to the peak month for this reason.</li>
<li><b>APAC SLA dipped below 95% from June to August</b>: four engineers left in May and utilization went past the ~{s['utilization_ceiling']:.0%} level where first reply SLA holds. Backfills started in September and SLA recovered.</li>
<li><b>FY28 plan: {caps.loc[TODAY, 'fte_needed']:.0f} engineers at today's AI footprint, {caps.loc['AI scaled to all accounts', 'fte_needed']:.0f} if self-service reaches every account</b>, against {caps.loc['No AI', 'fte_needed']:.0f} without AI. Sized to the peak month at the {s['utilization_ceiling']:.0%} utilization ceiling.</li>
<li><b>AI self-service is real but smaller than the bot says.</b> Rollout accounts filed {-ss['howto_config_tickets']['pct'] * 100:.0f}% fewer how-to and config tickets than the holdout (95% CI {-ss['howto_config_tickets']['ci'][1] * 100:.0f}% to {-ss['howto_config_tickets']['ci'][0] * 100:.0f}%): about {ss['tickets_avoided_per_month']:.0f} tickets a month. The assistant marks {ss['bot_claimed_per_month']:,.0f} conversations a month as resolved.</li>
<li><b>Agent assist saves {-aa['effect_when_used'] * 100:.0f}% of handle time when used</b>, not the {-aa['naive_used_vs_not'] * 100:.0f}% a used vs not used comparison shows: engineers reach for it on easy tickets.</li>
<li><b>{s['top_quality_stage']} is the most expensive stage for product quality</b>: {money(s['top_quality_cost'])} of support time in 12 months went to tickets that ended in an engineering escalation.</li>
<li><b>{ch['flagged']} renewing accounts ({money(ch['arr_flagged'])} ARR) flagged at risk</b> by a support signal model (test AUC {ch['test_auc']:.2f}, top decile holds {ch['top_decile_share_of_churn']:.0%} of churn).</li>
</ul>
<div class='grid'><div>{img('mbr')}</div><div>{img('ceiling')}</div></div>
<h2>Support economics</h2>
<div class='grid'><div>{img('heatmap')}</div><div>{img('quality')}</div></div>
<div class='wrap'>{qtab}</div>
<h2>AI ROI</h2>
<div class='grid'><div>{img('selfservice')}</div><div>{img('assist')}</div></div>
<h2>At-risk renewals (next four months)</h2>
<div class='wrap'>{risk}</div>
<h2>FY28 capacity plan (Feb 2027 to Jan 2028)</h2>
<div class='wrap'>{cap}</div>
<h2>Team scorecard, Aug to Oct 2026</h2>
<div class='wrap'>{sct}</div>
<h2>Alerts since April 2026</h2>
<div class='wrap'>{alert_html}</div>
</main></body></html>"""
