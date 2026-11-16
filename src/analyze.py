"""Build the warehouse, run data tests, answer the role's questions, write charts + MBR.

  1. Support economics   cost per ticket by product stage x plan, product quality cost
  2. AI ROI              self-service deflection (rollout vs holdout) and agent assist (staggered rollout)
  3. At-risk customers   churn model on support signals, time split, live at-risk list
  4. Capacity / AOP      FY28 headcount by region under scenarios, utilization ceiling from the data
  5. Scorecards, alerts, MBR one-pager
"""
import base64
import io
import json
from pathlib import Path

import duckdb
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
SLA_TARGET = 0.95
ORANGE, PURPLE, GREY, TEAL = "#E24329", "#6B4FBB", "#8C8C8C", "#2E8B87"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})


def build(con):
    for f in ["models/staging/stg_sources.sql", "models/intermediate/int_org_account_map.sql",
              "models/marts/core.sql", "models/marts/analysis_marts.sql"]:
        con.execute((ROOT / f).read_text())
    sql = (ROOT / "tests" / "data_tests.sql").read_text()
    results = []
    for block in sql.split("-- test:")[1:]:
        name, q = block.split("\n", 1)
        n = len(con.sql(q.strip().rstrip(";")).fetchall())
        results.append({"test": name.strip(), "failing_rows": n})
    res = pd.DataFrame(results)
    print(res.to_string(index=False))
    assert res.failing_rows.sum() == 0, "data tests failed"
    return res


def q(con, s):
    return con.execute(s).df()


# ---------------------------------------------------------------- 1. support economics
def economics(con):
    cost = q(con, "select * from mart_stage_plan_cost")
    qual = q(con, "select * from mart_stage_quality order by quality_cost desc")
    match = q(con, "select match_rule, count(*) n from int_org_account_map group by 1 order by 2 desc")
    return cost, qual, match


# ---------------------------------------------------------------- 2a. self-service deflection
def did(panel, col):
    g = panel.groupby(["self_service_cohort", "post"])[col].mean()
    t_pre, t_post = g["rollout", False], g["rollout", True]
    h_pre, h_post = g["holdout", False], g["holdout", True]
    effect = (t_post - t_pre) - (h_post - h_pre)
    counterfactual = t_pre * (h_post / h_pre)
    return effect, effect / counterfactual, t_post / t_pre - 1


def self_service(con, rng):
    p = q(con, "select * from mart_self_service_panel")
    out = {}
    for col in ["howto_config_tickets", "other_tickets", "tickets"]:
        eff, pct, naive = did(p, col)
        # account-level cluster bootstrap: resample accounts, keep all their months
        a = p.groupby(["account_id", "self_service_cohort", "post"])[col].agg(["sum", "count"]).unstack("post", fill_value=0)
        a = a.reset_index()
        treated = (a.self_service_cohort == "rollout").to_numpy()
        S0, S1 = a[("sum", False)].to_numpy(), a[("sum", True)].to_numpy()
        C0, C1 = a[("count", False)].to_numpy(), a[("count", True)].to_numpy()
        boots = []
        for _ in range(1000):
            w = np.bincount(rng.integers(0, len(a), len(a)), minlength=len(a))
            m = {}
            for g, mask in (("t", treated), ("h", ~treated)):
                ww = w * mask
                m[g] = ((S0 * ww).sum() / (C0 * ww).sum(), (S1 * ww).sum() / (C1 * ww).sum())
            e = (m["t"][1] - m["t"][0]) - (m["h"][1] - m["h"][0])
            boots.append(e / (m["t"][0] * m["h"][1] / m["h"][0]))
        out[col] = {"did_per_account_month": eff, "pct": pct, "ci": np.percentile(boots, [2.5, 97.5]).tolist(),
                    "naive_pre_post_pct": naive}
    live = p[(p.self_service_cohort == "rollout") & p.post]
    avoided_per_month = -out["howto_config_tickets"]["did_per_account_month"] * live.groupby("month").size().mean()
    unit = q(con, """select avg(loaded_cost) c, avg(direct_cost) d, avg(agent_work_minutes) m from fct_tickets
                     where category in ('how-to','configuration') and ticket_month >= date '2025-11-01'""").iloc[0]
    conv = q(con, """select count(*) conversations, sum(bot_marked_resolved::int) bot_resolved,
                     count(distinct conv_month) n_months from stg_self_service""").iloc[0]
    out["tickets_avoided_per_month"] = avoided_per_month
    out["bot_claimed_per_month"] = conv.bot_resolved / conv.n_months
    out["conversations_per_month"] = conv.conversations / conv.n_months
    out["hours_freed_per_month"] = avoided_per_month * unit.m / 60
    out["value_per_month_loaded"] = avoided_per_month * unit.c
    out["value_per_month_direct"] = avoided_per_month * unit.d
    # monthly series for the chart
    s = (p.groupby(["month", "self_service_cohort"]).howto_config_tickets.mean().unstack() * 100).rolling(3).mean().dropna()
    return out, s


# ---------------------------------------------------------------- 2b. agent assist (staggered DiD)
def ols(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    XtX_inv = np.linalg.pinv(X.T @ X)
    meat = (X * resid[:, None] ** 2).T @ X
    se = np.sqrt(np.diag(XtX_inv @ meat @ XtX_inv))
    return beta, se


def agent_assist(con):
    t = q(con, """select region, ticket_month, category, priority, product_stage, deployment,
                  agent_work_minutes, ai_assist_used, assist_available from fct_tickets""")
    t["ly"] = np.log(t.agent_work_minutes)
    av = t[t.assist_available]
    naive = np.exp(av[av.ai_assist_used].ly.mean() - av[~av.ai_assist_used].ly.mean()) - 1
    fe = pd.get_dummies(t[["region", "category", "priority", "product_stage", "deployment"]].astype(str)
                        .assign(month=t.ticket_month.astype(str)), drop_first=True, dtype=float)
    X = np.column_stack([np.ones(len(t)), t.assist_available.astype(float), fe.to_numpy()])
    beta, se = ols(t.ly.to_numpy(), X)
    itt, itt_se = beta[1], se[1]
    usage = av.ai_assist_used.mean()
    tot = np.exp(itt / usage) - 1
    # event study by months since the region's launch
    start = {"EMEA": "2026-03-01", "AMER": "2026-06-01", "APAC": "2026-09-01"}
    t["rel"] = [(pd.Timestamp(m).year - pd.Timestamp(start[r]).year) * 12 + pd.Timestamp(m).month - pd.Timestamp(start[r]).month
                for m, r in zip(t.ticket_month, t.region)]
    rel = t.rel.clip(-6, 5)
    rd = pd.get_dummies(rel.astype(int), prefix="rel", dtype=float).drop(columns="rel_-1")
    X2 = np.column_stack([np.ones(len(t)), rd.to_numpy(), fe.to_numpy()])
    b2, s2 = ols(t.ly.to_numpy(), X2)
    es = pd.DataFrame({"rel": [int(c.split("_")[1]) for c in rd.columns], "effect": np.exp(b2[1:1 + rd.shape[1]]) - 1,
                       "lo": np.exp(b2[1:1 + rd.shape[1]] - 1.96 * s2[1:1 + rd.shape[1]]) - 1,
                       "hi": np.exp(b2[1:1 + rd.shape[1]] + 1.96 * s2[1:1 + rd.shape[1]]) - 1})
    es = pd.concat([es, pd.DataFrame([{"rel": -1, "effect": 0, "lo": 0, "hi": 0}])]).sort_values("rel")
    mix = av.groupby("ai_assist_used").category.apply(lambda s: s.isin(["how-to", "licensing", "configuration"]).mean())
    return {"naive_used_vs_not": naive, "itt_pct": np.exp(itt) - 1,
            "itt_ci": [np.exp(itt - 1.96 * itt_se) - 1, np.exp(itt + 1.96 * itt_se) - 1],
            "usage_rate": usage, "effect_when_used": tot,
            "easy_share_used": mix.get(True), "easy_share_not_used": mix.get(False)}, es


# ---------------------------------------------------------------- 3. churn
FEATS = ["tickets_per_100_seats", "emergencies_180d", "sla_breaches_180d", "escalations_180d",
         "bad_ssat_180d", "reopens_180d", "seats"]


def featurize(d):
    X = pd.DataFrame({
        "log_tickets_per_100_seats": np.log1p(d.tickets_per_100_seats),
        "any_emergency": (d.emergencies_180d > 0).astype(float),
        "sla_breaches": d.sla_breaches_180d.astype(float),
        "sqrt_escalations": np.sqrt(d.escalations_180d),
        "sqrt_bad_ssat": np.sqrt(d.bad_ssat_180d),
        "reopens": d.reopens_180d.astype(float),
        "log_seats": np.log(d.seats),
        "ultimate": (d.plan == "Ultimate").astype(float),
        "self_managed": (d.deployment == "Self-Managed").astype(float),
    })
    return X


def churn(con):
    r = q(con, "select * from mart_renewals")
    train, test = r[r.renewal_month < pd.Timestamp("2026-02-01")], r[r.renewal_month >= pd.Timestamp("2026-02-01")]
    sc = StandardScaler().fit(featurize(train))
    m = LogisticRegression(max_iter=1000).fit(sc.transform(featurize(train)), train.churned)
    p = m.predict_proba(sc.transform(featurize(test)))[:, 1]
    auc = roc_auc_score(test.churned, p)
    top = test.assign(p=p).sort_values("p", ascending=False)
    k = max(1, len(top) // 10)
    lift = top.head(k).churned.mean() / test.churned.mean()
    top_decile_share = top.head(k).churned.sum() / test.churned.sum()
    coefs = pd.Series(m.coef_[0], index=featurize(train).columns).sort_values()
    # live at-risk list: accounts renewing in the next four months
    cur = q(con, "select * from mart_account_current")
    Xc = sc.transform(featurize(cur))
    cur["churn_risk"] = m.predict_proba(Xc)[:, 1]
    contrib = pd.DataFrame(Xc * m.coef_[0], columns=featurize(cur).columns)
    labels = {"log_tickets_per_100_seats": "high ticket rate", "any_emergency": "emergency ticket",
              "sla_breaches": "SLA breaches", "sqrt_escalations": "engineering escalations",
              "sqrt_bad_ssat": "bad SSAT", "reopens": "reopened tickets", "log_seats": "small account",
              "ultimate": "plan", "self_managed": "self-managed"}
    cur["drivers"] = [", ".join(labels[c] for c in row.nlargest(2).index if row[c] > 0.15) for _, row in contrib.iterrows()]
    soon = cur[(cur.next_renewal >= pd.Timestamp("2026-11-01")) & (cur.next_renewal <= pd.Timestamp("2027-02-01"))]
    base = r.churned.mean()
    at_risk = soon[soon.churn_risk >= 2 * base].sort_values("arr", ascending=False)
    return {"renewals_train": len(train), "renewals_test": len(test), "base_churn": base,
            "test_auc": auc, "top_decile_lift": lift, "top_decile_share_of_churn": top_decile_share,
            "renewing_next_4m": len(soon), "flagged": len(at_risk), "arr_flagged": float(at_risk.arr.sum()),
            "coefs": coefs.round(3).to_dict()}, at_risk


# ---------------------------------------------------------------- 4. capacity plan
def capacity(con, ss, aa):
    rm = q(con, "select * from mart_support_cost_month order by region, month")
    sla = q(con, """select region, ticket_month as "month", 1 - avg(sla_breached::int) sla from fct_tickets group by all""")
    rm = rm.merge(sla, on=["region", "month"])
    # utilization ceiling: highest utilization band where SLA attainment still meets 95%
    # = right edge of the last 2.5-point band before the first band whose average misses 95%
    bands = pd.cut(rm.utilization, np.arange(0.30, 0.80, 0.025))
    curve = rm.groupby(bands, observed=True).sla.agg(["mean", "size"])
    ceiling = 0.30
    for b, row in curve.iterrows():
        if row["size"] >= 2 and row["mean"] < SLA_TARGET:
            break
        ceiling = float(b.right)
    curve = curve["mean"]
    # volume trend per region: last 12 months, linear in month index
    plan = []
    mix = q(con, """select region, avg((category in ('how-to','configuration'))::int) hc,
                    avg(case when ticket_month >= date '2026-08-01' then agent_work_minutes end) mins3,
                    avg(case when ticket_month >= date '2026-08-01' then assist_available::int end) avail3,
                    avg(cost_per_hour) cph
                    from fct_tickets join stg_agents using (agent_id, region)
                    where ticket_month >= date '2025-11-01' group by 1""").set_index("region")
    fy28 = pd.period_range("2027-02", "2028-01", freq="M")
    for r, g in rm.groupby("region"):
        g = g.reset_index(drop=True)
        hc, cph = mix.loc[r, ["hc", "cph"]]
        defl = -ss["howto_config_tickets"]["pct"] * hc          # share of all tickets self-service removes
        # volume as if no AI: add back the tickets self-service removed from the rollout half since Feb 2026
        post = g.month >= pd.Timestamp("2026-02-01")
        vol_no_ai = g.tickets / np.where(post, 1 - defl * 0.5, 1.0)
        slope, icpt = np.polyfit(np.arange(len(g)), vol_no_ai, 1)
        base_vol = icpt + slope * np.arange(27, 39)           # Nov 2024 = 0, Feb 2027 = 27
        # handle time as if no AI: last 3 months, assist effect removed for tickets where it was live
        mins_now, avail = mix.loc[r, ["mins3", "avail3"]]
        mins_no_ai = mins_now / (1 + aa["itt_pct"] * avail)
        for scen, vol_mult, deflect, assist in [
            ("No AI", 1.0, 0.0, 0.0),
            ("Today's AI footprint", 1.0, defl * 0.5, -aa["itt_pct"]),
            ("AI scaled to all accounts", 1.0, defl, -aa["itt_pct"]),
            ("High growth +15%, AI scaled", 1.15, defl, -aa["itt_pct"]),
        ]:
            vol = base_vol * vol_mult * (1 - deflect)
            hours = vol * mins_no_ai / 60 * (1 - assist)
            fte = hours.max() / (21 * 8 * ceiling)
            plan.append({"region": r, "scenario": scen, "fy28_tickets": vol.sum(), "fy28_work_hours": hours.sum(),
                         "fte_needed": fte, "current_fte": int(g.engineers.iloc[-1]),
                         "fy28_cost": np.ceil(fte) * cph * 21 * 8 * 12})
    plan = pd.DataFrame(plan)
    return plan, ceiling, rm, curve


# ---------------------------------------------------------------- charts
def png(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(b.getvalue()).decode()


def save(fig, name):
    fig.savefig(REP / name, dpi=150, bbox_inches="tight")
    return png(fig)


def charts(cost, qual, ss_series, es, rm, curve, ceiling, mbr, alerts):
    imgs = {}
    # heatmap: cost per ticket, stage x plan, annotated with tickets
    piv = cost.pivot(index="product_stage", columns="plan", values="support_cost").fillna(0)
    piv = piv.loc[piv.sum(1).sort_values(ascending=False).index]
    cpt = cost.pivot(index="product_stage", columns="plan", values="cost_per_ticket").reindex(piv.index)
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    im = ax.imshow(piv / 1e3, cmap="Oranges", aspect="auto")
    ax.set_xticks(range(len(piv.columns)), piv.columns)
    ax.set_yticks(range(len(piv.index)), piv.index)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            ax.text(j, i, f"${piv.iat[i, j] / 1e3:,.0f}k\n${cpt.iat[i, j]:,.0f}/ticket", ha="center", va="center", fontsize=7,
                    color="white" if piv.iat[i, j] > piv.values.max() * 0.55 else "black")
    ax.set_title("Support cost, last 12 months, by product stage and plan")
    fig.colorbar(im, ax=ax, label="$k")
    imgs["heatmap"] = save(fig, "cost_heatmap_stage_plan.png")

    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    qq = qual.sort_values("quality_cost")
    ax.barh(qq.product_stage, qq.quality_cost / 1e3, color=ORANGE)
    for y, (v, n) in enumerate(zip(qq.quality_cost / 1e3, qq.real_customer_issues)):
        ax.text(v, y, f"  {int(n):,} real customer issues" if pd.notna(n) else "", va="center", fontsize=7, color=GREY)
    ax.set_xlabel("$k support cost on tickets escalated to engineering (12 mo)")
    ax.set_title("Product quality cost by stage")
    imgs["quality"] = save(fig, "product_quality_cost.png")

    fig, ax = plt.subplots(figsize=(6, 3))
    x = ss_series.index.astype(str)
    ax.plot(x, ss_series["holdout"], color=GREY, label="Holdout (no assistant)")
    ax.plot(x, ss_series["rollout"], color=ORANGE, label="Rollout cohort")
    ax.axvline(next(i for i, v in enumerate(x) if v.startswith("2026-02")), color=PURPLE, ls="--", lw=1)
    ax.set_xticks(range(0, len(x), 3), [s[:7] for s in x[::3]], rotation=0)
    ax.set_ylabel("How-to + config tickets per\n100 accounts (3 mo avg)")
    ax.set_title("AI self-service: rollout vs holdout")
    ax.legend(frameon=False)
    imgs["selfservice"] = save(fig, "self_service_did.png")

    fig, ax = plt.subplots(figsize=(6, 3))
    ax.axhline(0, color=GREY, lw=0.8)
    ax.axvline(-0.5, color=PURPLE, ls="--", lw=1)
    ax.errorbar(es.rel, es.effect * 100, yerr=[(es.effect - es.lo) * 100, (es.hi - es.effect) * 100], fmt="o", color=ORANGE, capsize=2)
    ax.set_xlabel("Months since agent assist launched in the region")
    ax.set_ylabel("Handle time vs month -1 (%)")
    ax.set_title("Agent assist: staggered rollout event study (all tickets)")
    imgs["assist"] = save(fig, "agent_assist_event_study.png")

    fig, ax = plt.subplots(figsize=(5, 3))
    for r, c in zip(["AMER", "EMEA", "APAC"], [PURPLE, TEAL, ORANGE]):
        g = rm[rm.region == r]
        ax.scatter(g.utilization * 100, g.sla * 100, s=14, color=c, label=r)
    ax.axhline(95, color=GREY, ls="--", lw=1)
    ax.axvline(ceiling * 100, color=ORANGE, ls=":", lw=1)
    ax.set_xlabel("Utilization (ticket hours / paid hours, %)")
    ax.set_ylabel("First reply SLA met (%)")
    ax.set_title(f"SLA holds up to ~{ceiling:.0%} utilization")
    ax.legend(frameon=False)
    imgs["ceiling"] = save(fig, "utilization_vs_sla.png")

    fig, ax1 = plt.subplots(figsize=(6, 3))
    x = mbr.month.dt.strftime("%Y-%m")
    ax1.bar(x, mbr.cost_per_ticket, color="#F6C1A8")
    ax1.set_ylabel("Cost per ticket ($)")
    ax2 = ax1.twinx()
    ax2.plot(x, mbr.sla_attainment * 100, color=PURPLE, label="SLA met %")
    ax2.plot(x, mbr.ssat * 100, color=TEAL, label="SSAT %")
    ax2.axhline(95, color=GREY, ls="--", lw=0.8)
    ax2.set_ylim(80, 100)
    ax2.legend(frameon=False, loc="lower left")
    ax1.set_xticks(range(0, len(x), 3), list(x)[::3])
    ax1.set_title("Monthly cost per ticket, SLA and SSAT")
    imgs["mbr"] = save(fig, "mbr_trend.png")
    return imgs


def main():
    REP.mkdir(exist_ok=True)
    rng = np.random.default_rng(1)
    con = duckdb.connect(str(ROOT / "data" / "warehouse.duckdb"))
    import os
    os.chdir(ROOT)
    tests = build(con)
    cost, qual, match = economics(con)
    ss, ss_series = self_service(con, rng)
    aa, es = agent_assist(con)
    ch, at_risk = churn(con)
    plan, ceiling, rm, curve = capacity(con, ss, aa)
    mbr = q(con, "select * from mart_mbr_month")
    alerts = q(con, "select * from mart_alerts order by week")
    scorecard = q(con, """select region, agent_id, sum(tickets_solved) tickets, avg(utilization) utilization,
                          avg(sla_attainment) sla, avg(ssat) ssat, avg(reopen_rate) reopen, avg(ai_assist_rate) ai_rate
                          from mart_agent_month where month >= date '2026-08-01' group by all""")
    imgs = charts(cost, qual, ss_series, es, rm, curve, ceiling, mbr, alerts)

    truth = pd.read_csv(ROOT / "data" / "planted_truth.csv").set_index("truth").value
    last12 = mbr[mbr.month >= pd.Timestamp("2025-11-01")]
    summary = {
        "tickets_total": int(mbr.tickets.sum()),
        "real_issue_count": int(q(con, "select count(*) n from stg_real_issues").n.iloc[0]),
        "last12_support_cost": float(last12.support_cost.sum()),
        "last12_cost_per_ticket": float(last12.support_cost.sum() / last12.tickets.sum()),
        "data_tests": len(tests),
        "org_match_rules": match.set_index("match_rule").n.to_dict(),
        "self_service": {k: v for k, v in ss.items()},
        "self_service_truth_blended": float(
            (truth["self_service_deflection_howto"] + truth["self_service_deflection_config"]) / 2),
        "agent_assist": aa,
        "agent_assist_truth_when_used": float(-truth["agent_assist_handle_time_cut_when_used"]),
        "churn": ch,
        "utilization_ceiling": ceiling,
        "capacity": plan.groupby("scenario")[["fte_needed", "fy28_cost", "fy28_tickets"]].sum().round(1).to_dict("index"),
        "current_fte": int(plan.groupby("region").current_fte.first().sum()),
        "alerts": len(alerts),
        "top_quality_stage": qual.iloc[0].product_stage,
        "top_quality_cost": float(qual.iloc[0].quality_cost),
        "quality_cost_total": float(qual.quality_cost.sum()),
    }
    json.dump(summary, open(REP / "summary.json", "w"), indent=2, default=float)
    plan.to_csv(REP / "capacity_plan_fy28.csv", index=False)
    at_risk.to_csv(REP / "at_risk_accounts.csv", index=False)
    cost.to_csv(REP / "cost_by_stage_plan.csv", index=False)
    qual.to_csv(REP / "product_quality_cost.csv", index=False)
    alerts.to_csv(REP / "alerts.csv", index=False)
    scorecard.to_csv(REP / "engineer_scorecard_last_quarter.csv", index=False)
    print(json.dumps(summary, indent=1, default=float))

    from mbr import render
    (REP / "mbr.html").write_text(render(summary, imgs, plan, at_risk, alerts, qual, mbr, scorecard), encoding="utf-8")
    print("wrote reports/mbr.html")


if __name__ == "__main__":
    main()
