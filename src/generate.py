"""Generate the source-system extracts a Support Engineering analyst would work from.

Ticket-level support data is private, so the tickets are simulated. What is real:
  * product stage mix, bug share and issue complexity per stage come from public
    gitlab-org/gitlab issues labeled `customer` (data/real, see fetch_gitlab_issues.py)
  * first-reply targets follow GitLab's Priority Support impact levels
  * KPI targets (SLA 95%, SSAT 95%) come from GitLab's Support performance indicators page

Every number the analysis later recovers (AI deflection, agent-assist effect, churn
drivers) is planted here with a known true value, so the method can be checked.

Writes data/sources/*.csv, one file per source system, each with its own keys.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "real" / "gitlab_customer_issues.csv"
OUT = ROOT / "data" / "sources"
SEED = 7
rng = np.random.default_rng(SEED)

MONTHS = pd.period_range("2024-11", "2026-10", freq="M")
N_ACCOUNTS = 1500
REGIONS = ["AMER", "EMEA", "APAC"]
REGION_P = [0.50, 0.34, 0.16]
COST_PER_HOUR = {"AMER": 92.0, "EMEA": 78.0, "APAC": 44.0}       # fully loaded, USD
SLA_MIN = {"Emergency": 30, "High": 240, "Medium": 480, "Low": 1440}  # first reply targets
PRIORITY_P = [0.03, 0.17, 0.45, 0.35]
PRIORITIES = list(SLA_MIN)
CATEGORIES = ["bug", "how-to", "configuration", "performance", "upgrade", "licensing"]

# planted truths (recovered later by the analysis)
TRUE_DEFLECT = {"how-to": 0.26, "configuration": 0.18}          # share of tickets removed by self-service
TRUE_ASSIST_EFFECT = 0.14                                        # handle time cut when an agent uses AI
SELF_SERVICE_START = pd.Period("2026-02", "M")
ASSIST_START = {"EMEA": pd.Period("2026-03", "M"), "AMER": pd.Period("2026-06", "M"),
                "APAC": pd.Period("2026-09", "M")}


def stage_profile():
    """Stage mix, bug share and complexity from real gitlab-org customer issues."""
    real = pd.read_csv(REAL)
    real = real[real.stage.notna() & (real.stage != "")]
    # GitLab split Secure and AI into several devops:: labels during 2025-26; fold them back
    s = real.stage.str.replace(r"\s*\[deprecated\]", "", case=False, regex=True)
    s = np.where(s.str.contains("secur|govern|supply chain"), "secure & govern", s)
    s = np.where(pd.Series(s).str.contains("^ai|agent|duo", regex=True), "ai-powered", s)
    real["stage_g0"] = s
    top = real.stage_g0.value_counts().head(10).index
    real["stage_g"] = np.where(real.stage_g0.isin(top), real.stage_g0, "other")
    OUT.mkdir(parents=True, exist_ok=True)
    real[["stage", "stage_g"]].drop_duplicates().to_csv(OUT / "stage_map.csv", index=False)
    g = real.groupby("stage_g").agg(n=("iid", "size"),
                                    bug_share=("type", lambda s: (s == "bug").mean()),
                                    notes=("notes", "median"))
    g["share"] = g.n / g.n.sum()
    g["complexity"] = (g.notes / g.notes.median()).clip(0.7, 1.5)
    return g


def h(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


def accounts():
    n = N_ACCOUNTS
    tier = rng.choice(["Premium", "Ultimate"], n, p=[0.66, 0.34])
    deploy = rng.choice(["GitLab.com", "Self-Managed", "Dedicated"], n, p=[0.45, 0.50, 0.05])
    seats = np.round(np.exp(rng.normal(np.where(tier == "Ultimate", 6.2, 4.9), 0.9))).astype(int) + 5
    price = np.where(tier == "Ultimate", 1188, 348)
    start = pd.to_datetime("2019-01-01") + pd.to_timedelta(rng.integers(0, 2830, n), unit="D")
    # a third of the book lands during the window (growth)
    a = pd.DataFrame({
        "account_key": [f"ACC{i:05d}" for i in range(n)],
        "name": [f"{w1} {w2}" for w1, w2 in zip(rng.choice(NAME1, n), rng.choice(NAME2, n))],
        "tier": tier, "deployment": deploy, "seats": seats, "arr": seats * price,
        "region": rng.choice(REGIONS, n, p=REGION_P), "start_date": start,
        "health": rng.normal(0, 1, n),                       # latent product fit, never exported
    })
    a["name"] = a.name + " " + a.account_key.str[-4:]
    a["domain"] = a.name.str.lower().str.replace(r"[^a-z0-9]+", "", regex=True) + ".com"
    a["treated_self_service"] = [h(k) % 2 == 0 for k in a.account_key]
    return a


NAME1 = ["Acme", "Northwind", "Globex", "Initech", "Umbrella", "Stark", "Wayne", "Hooli", "Vandelay",
         "Soylent", "Tyrell", "Cyberdyne", "Aperture", "Massive", "Pied", "Gringotts", "Wonka", "Oscorp",
         "Monarch", "Nakatomi", "Blue Sun", "Duff", "Ollivander", "Prestige", "Sterling", "Zenith"]
NAME2 = ["Systems", "Labs", "Bank", "Health", "Logistics", "Software", "Retail", "Energy", "Telecom",
         "Insurance", "Motors", "Media", "Analytics", "Foods", "Group", "Cloud"]


def agents():
    rows, i = [], 0
    # headcount at the start of the window, then steady hiring that keeps pace with demand
    plan = {"AMER": (34, 12), "EMEA": (24, 9), "APAC": (12, 4)}
    for r, (n0, growth) in plan.items():
        for k in range(n0 + growth):
            if k < n0:
                hire = pd.Timestamp("2019-06-01") + pd.to_timedelta(int(rng.integers(0, 1900)), unit="D")
            else:  # growth hires spread evenly across the 24 months
                hire = (MONTHS[0] + int((k - n0 + 0.5) * 24 / growth)).to_timestamp()
            rows.append({"agent_id": f"SE{i:03d}", "region": r, "hire_date": hire,
                         "leave_date": pd.NaT, "cost_per_hour": COST_PER_HOUR[r] * rng.uniform(0.9, 1.1)})
            i += 1
    ag = pd.DataFrame(rows)
    # APAC loses four engineers in May 2026, backfilled only in September: the coverage gap
    apac = ag[(ag.region == "APAC") & (ag.hire_date < "2025-01-01")].index[:4]
    ag.loc[apac, "leave_date"] = pd.Timestamp("2026-05-15")
    for j in range(4):
        ag.loc[len(ag)] = {"agent_id": f"SE{i + j:03d}", "region": "APAC", "hire_date": pd.Timestamp("2026-09-01"),
                           "leave_date": pd.NaT, "cost_per_hour": COST_PER_HOUR["APAC"]}
    return ag


def tickets_and_renewals(acc, ag, prof):
    stages, s_share = prof.index.to_numpy(), prof.share.to_numpy()
    rows, renewals = [], []
    active = acc.start_date <= MONTHS[0].to_timestamp()
    churned = pd.Series(False, index=acc.index)
    hist = []  # per-account recent support signals
    base_rate = 1.35 * (acc.seats ** 0.35) / np.mean(acc.seats ** 0.35)
    base_rate *= np.where(acc.deployment == "Self-Managed", 1.5, np.where(acc.deployment == "Dedicated", 1.2, 0.8))
    base_rate *= np.where(acc.tier == "Ultimate", 1.25, 1.0) * np.exp(-0.25 * acc.health)
    tid = 0
    for mi, m in enumerate(MONTHS):
        ms = m.to_timestamp()
        active = (acc.start_date <= ms + pd.offsets.MonthEnd(0)) & ~churned
        growth = 1 + 0.010 * mi                                   # more product, more questions
        lam = base_rate * growth * (1.12 if m.month in (1, 3, 10) else 1.0)
        n = rng.poisson(np.where(active, lam, 0))
        idx = np.repeat(acc.index.to_numpy(), n)
        if len(idx):
            t = pd.DataFrame({"acc": idx})
            t["month"] = m
            t["stage"] = rng.choice(stages, len(t), p=s_share)
            bug_p = prof.bug_share.reindex(t.stage).to_numpy()
            dep = acc.deployment.to_numpy()[idx]
            cat_p = np.column_stack([bug_p * 0.55, np.full(len(t), 0.30), np.full(len(t), 0.22),
                                     np.full(len(t), 0.08), np.where(dep == "Self-Managed", 0.16, 0.02),
                                     np.full(len(t), 0.07)])
            cat_p /= cat_p.sum(1, keepdims=True)
            t["category"] = [CATEGORIES[k] for k in (cat_p.cumsum(1) > rng.random((len(t), 1))).argmax(1)]
            # self-service deflection: treated accounts lose a share of how-to / config tickets
            treated = acc.treated_self_service.to_numpy()[idx] & (m >= SELF_SERVICE_START)
            drop = treated & (rng.random(len(t)) < t.category.map(TRUE_DEFLECT).fillna(0.0).to_numpy())
            t = t[~drop].copy()
            hist.append(t[["acc", "month", "category"]])
            t["priority"] = rng.choice(PRIORITIES, len(t), p=PRIORITY_P)
            rows.append(t)
        # renewals this month: churn depends on the last 6 months of support experience (filled in later)
        ren = acc.index[active & (acc.start_date.dt.month == m.month) & (acc.start_date < ms - pd.Timedelta(days=200))]
        renewals.append(pd.DataFrame({"acc": ren, "month": m}))
        # churn decided after FRT/SSAT are known, so defer: approximate with ticket volume + health now,
        # exact support-driven churn applied in finalize() below via a second pass
        churned_now = ren[rng.random(len(ren)) < 0]  # placeholder, real decision in finalize
        churned.loc[churned_now] = True
    t = pd.concat(rows, ignore_index=True)
    t["ticket_id"] = np.arange(100000, 100000 + len(t))
    return t, pd.concat(renewals, ignore_index=True)


def finalize(t, acc, ag, prof, renewals):
    n = len(t)
    t["region"] = acc.region.to_numpy()[t.acc]
    t["tier"] = acc.tier.to_numpy()[t.acc]
    t["deployment"] = acc.deployment.to_numpy()[t.acc]
    day = rng.integers(0, 28, n)
    hour = np.clip(rng.normal(14, 3, n), 0, 23.9)
    t["created_at"] = [m.to_timestamp() + pd.Timedelta(days=int(d), hours=float(hh)) for m, d, hh in zip(t.month, day, hour)]
    # handle time (agent minutes across the ticket's life)
    cat_min = {"bug": 210, "how-to": 63, "configuration": 112, "performance": 240, "upgrade": 180, "licensing": 42}
    pri_mult = {"Emergency": 2.6, "High": 1.6, "Medium": 1.0, "Low": 0.75}
    mi = np.array([MONTHS.get_loc(m) for m in t.month])
    base = (t.category.map(cat_min) * t.priority.map(pri_mult) * prof.complexity.reindex(t.stage).to_numpy()
            * np.where(t.deployment == "Self-Managed", 1.15, 1.0) * (1 + 0.006 * mi))
    t["assist_available"] = [m >= ASSIST_START[r] for m, r in zip(t.month, t.region)]
    easy = t.category.isin(["how-to", "licensing", "configuration"]) & t.priority.isin(["Low", "Medium"])
    p_use = np.where(easy, 0.80, 0.35)
    t["ai_assist_used"] = t.assist_available & (rng.random(n) < p_use)
    eff = np.where(t.ai_assist_used, 1 - TRUE_ASSIST_EFFECT, 1.0)
    t["handle_minutes"] = np.round(base * eff * np.exp(rng.normal(0, 0.45, n)), 1)
    # assign to an agent active in the region that month
    agent = []
    for (r, m), g in t.groupby(["region", "month"]).groups.items():
        ms = m.to_timestamp()
        pool = ag[(ag.region == r) & (ag.hire_date <= ms) & (ag.leave_date.isna() | (ag.leave_date > ms))].agent_id.to_numpy()
        agent.append(pd.Series(rng.choice(pool, len(g)), index=g))
    t["agent_id"] = pd.concat(agent).sort_index()
    # load = demand hours / ticket capacity hours in region-month drives first reply time
    cap = {}
    for r in REGIONS:
        for m in MONTHS:
            ms = m.to_timestamp()
            heads = ((ag.region == r) & (ag.hire_date <= ms) & (ag.leave_date.isna() | (ag.leave_date > ms))).sum()
            cap[(r, m)] = heads * 21 * 8 * 0.85   # paid hours less 15% for leave, training and meetings
    demand = t.groupby(["region", "month"]).handle_minutes.sum() / 60
    load = pd.Series({k: demand.get(k, 0) / v for k, v in cap.items()})
    t["load"] = [load[(r, m)] for r, m in zip(t.region, t.month)]
    target = t.priority.map(SLA_MIN).to_numpy()
    frt = target * np.exp(rng.normal(-1.05, 0.55, n) + 3.0 * np.clip(t.load - 0.68, 0, None))
    frt = np.where((t.region == "APAC") & (t.priority == "Emergency"), frt * 1.3, frt)
    t["first_reply_minutes"] = np.round(frt, 1)
    t["sla_target_minutes"] = target
    t["sla_breached"] = t.first_reply_minutes > t.sla_target_minutes
    t["escalated_to_dev"] = (t.category == "bug") & (rng.random(n) < 0.45) | (t.category == "performance") & (rng.random(n) < 0.2)
    t["reopened"] = rng.random(n) < (0.06 + 0.06 * (t.category == "bug"))
    good_logit = 3.4 - 1.6 * t.sla_breached - 1.1 * t.reopened - 0.5 * t.escalated_to_dev - 0.35 * np.log(t.handle_minutes / 60).clip(0)
    responded = rng.random(n) < 0.36
    t["ssat"] = np.where(responded, np.where(rng.random(n) < 1 / (1 + np.exp(-good_logit)), "good", "bad"), None)
    t["solved_at"] = t.created_at + pd.to_timedelta(t.handle_minutes * rng.uniform(8, 40, n), unit="m")

    # renewals: churn driven by support experience in the 180 days before renewal + latent health
    t["acc_month"] = t.month
    out = []
    churned = set()
    for _, r in renewals.sort_values("month").iterrows():
        if r.acc in churned:
            continue
        lo = r.month - 6
        w = t[(t.acc == r.acc) & (t.month >= lo) & (t.month < r.month)]
        a = acc.loc[r.acc]
        z = (-3.7 - 0.55 * a.health + 1.0 * (w.priority == "Emergency").any() + 0.22 * w.sla_breached.sum()
             + 0.35 * w.escalated_to_dev.sum() ** 0.5 + 1.3 * (w.ssat == "bad").sum() ** 0.5 - 0.0004 * min(a.seats, 2000))
        ch = rng.random() < 1 / (1 + np.exp(-z))
        out.append({"acc": r.acc, "renewal_month": r.month, "churned": ch})
        if ch:
            churned.add(r.acc)
    ren = pd.DataFrame(out)
    # drop tickets filed after an account churned
    ch_month = ren[ren.churned].set_index("acc").renewal_month
    after = t.acc.map(ch_month)
    t = t[after.isna() | (t.month < after)].copy()
    return t, ren


def export(acc, ag, t, ren, prof):
    OUT.mkdir(parents=True, exist_ok=True)
    # billing system: subscription keyed, knows plan, seats, ARR, renewal outcome
    sub = acc[["account_key", "tier", "seats", "arr", "start_date", "deployment"]].copy()
    sub["subscription_id"] = ["A-S" + str(10000000 + h(k) % 89999999) for k in acc.account_key]
    sub["sfdc_account_id"] = ["001" + hashlib.md5(("sf" + k).encode()).hexdigest().upper()[:12] for k in acc.account_key]
    last = ren.groupby("acc").agg(churned=("churned", "max"), last_renewal=("renewal_month", "max"))
    sub["status"] = np.where(sub.index.map(last.churned).fillna(False).astype(bool), "cancelled", "active")
    sub["cancelled_month"] = [str(last.last_renewal.get(i)) if s == "cancelled" else "" for i, s in zip(sub.index, sub.status)]
    sub.rename(columns={"tier": "plan", "start_date": "term_start"})[[
        "subscription_id", "sfdc_account_id", "plan", "deployment", "seats", "arr", "term_start", "status", "cancelled_month"
    ]].to_csv(OUT / "billing_subscriptions.csv", index=False)
    # CRM: account keyed, region, name; knows zendesk org id for most but not all accounts
    crm = pd.DataFrame({"sfdc_account_id": sub.sfdc_account_id, "account_name": acc.name, "region": acc.region,
                        "website": "https://www." + acc.domain})
    crm["zendesk_org_id"] = [str(800000 + i) if rng.random() < 0.82 else "" for i in acc.index]
    crm.to_csv(OUT / "crm_accounts.csv", index=False)
    # Zendesk: org keyed, messy names, 4% of accounts have a duplicate org from a second email domain
    zd = pd.DataFrame({"org_id": [str(800000 + i) for i in acc.index],
                       "org_name": [n.upper() + ", INC." if rng.random() < 0.3 else n for n in acc.name],
                       "domain_names": acc.domain, "sfdc_id": [s if rng.random() < 0.6 else "" for s in sub.sfdc_account_id]})
    dup_idx = acc.sample(frac=0.04, random_state=SEED).index
    dups = pd.DataFrame({"org_id": [str(900000 + i) for i in dup_idx], "org_name": acc.loc[dup_idx, "name"] + " (EU)",
                         "domain_names": acc.loc[dup_idx, "domain"], "sfdc_id": ""})
    pd.concat([zd, dups]).to_csv(OUT / "zendesk_organizations.csv", index=False)
    # tickets point at the org; 30% of tickets from duplicate-org accounts land on the duplicate
    org = np.array([str(800000 + a) for a in t.acc])
    use_dup = t.acc.isin(dup_idx).to_numpy() & (rng.random(len(t)) < 0.3)
    org[use_dup] = [str(900000 + a) for a in t.acc[use_dup]]
    t_out = pd.DataFrame({
        "ticket_id": t.ticket_id, "organization_id": org, "created_at": t.created_at, "solved_at": t.solved_at,
        "priority": t.priority, "product_stage": t.stage, "category": t.category, "assignee_id": t.agent_id,
        "first_reply_minutes": t.first_reply_minutes, "sla_target_minutes": t.sla_target_minutes,
        "agent_work_minutes": t.handle_minutes, "ai_assist_used": t.ai_assist_used,
        "escalated_to_engineering": t.escalated_to_dev, "reopened": t.reopened, "satisfaction": t.ssat,
    })
    t_out.to_csv(OUT / "zendesk_tickets.csv", index=False)
    ag.to_csv(OUT / "agents.csv", index=False)
    # self-service assistant logs: conversations and the bot's own "resolved" flag (counts every thumbs up)
    conv = []
    for m in MONTHS[MONTHS >= SELF_SERVICE_START]:
        live = acc[acc.treated_self_service & (acc.start_date <= m.to_timestamp())]
        k = rng.poisson(1.6 * (live.seats ** 0.35) / np.mean(acc.seats ** 0.35))
        conv.append(pd.DataFrame({"org_id": np.repeat([str(800000 + i) for i in live.index], k), "month": str(m)}))
    conv = pd.concat(conv, ignore_index=True)
    conv["bot_marked_resolved"] = rng.random(len(conv)) < 0.52
    conv.to_csv(OUT / "self_service_conversations.csv", index=False)
    pd.DataFrame({"org_id": [str(800000 + i) for i in acc.index], "self_service_cohort":
                  np.where(acc.treated_self_service, "rollout", "holdout")}).to_csv(OUT / "experiment_assignment.csv", index=False)
    prof.reset_index().to_csv(OUT / "stage_profile_from_real_issues.csv", index=False)
    pd.DataFrame([{"truth": "self_service_deflection_howto", "value": TRUE_DEFLECT["how-to"]},
                  {"truth": "self_service_deflection_config", "value": TRUE_DEFLECT["configuration"]},
                  {"truth": "agent_assist_handle_time_cut_when_used", "value": TRUE_ASSIST_EFFECT}]).to_csv(
        ROOT / "data" / "planted_truth.csv", index=False)


def main():
    prof = stage_profile()
    acc = accounts()
    ag = agents()
    t, ren = tickets_and_renewals(acc, ag, prof)
    t, ren = finalize(t, acc, ag, prof, ren)
    export(acc, ag, t, ren, prof)
    print(f"{len(acc)} accounts, {len(ag)} engineers, {len(t)} tickets, {len(ren)} renewals, "
          f"{ren.churned.mean():.1%} renewal churn")


if __name__ == "__main__":
    main()
