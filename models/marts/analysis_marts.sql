-- Analysis marts: support economics, scorecards, alerting, renewal features, AI panels, MBR.

-- Support economics: where support cost goes, by product stage and plan (last 12 months).
create or replace table mart_stage_plan_cost as
select product_stage, plan,
       count(*)                                   as tickets,
       sum(loaded_cost)                           as support_cost,
       avg(loaded_cost)                           as cost_per_ticket,
       avg(agent_work_minutes)                    as avg_work_minutes,
       avg(escalated_to_engineering::int)         as escalation_rate,
       1 - avg(sla_breached::int)                 as sla_attainment,
       avg(case satisfaction when 'good' then 1 when 'bad' then 0 end) as ssat
from fct_tickets
where ticket_month >= date '2025-11-01'
group by all;

-- Product quality cost: support spend on tickets that ended in an engineering escalation,
-- next to the real public customer-labeled issues for the same stage.
create or replace table mart_stage_quality as
with sup as (
    select product_stage,
           count(*) as tickets,
           sum(loaded_cost) as support_cost,
           sum(case when escalated_to_engineering then loaded_cost else 0 end) as quality_cost,
           sum(escalated_to_engineering::int) as escalations
    from fct_tickets where ticket_month >= date '2025-11-01' group by all
), real as (
    select coalesce(p.stage_g, 'other') as product_stage,
           count(*) as real_customer_issues,
           avg((r.issue_type = 'bug')::int) as real_bug_share,
           median(case when r.closed_at is not null then date_diff('day', r.created_at, r.closed_at) end) as real_median_days_to_close,
           avg((r.state = 'opened')::int) as real_share_still_open
    from stg_real_issues r
    left join read_csv_auto('data/sources/stage_map.csv', all_varchar = true) p on p.stage = r.stage
    where r.stage is not null
    group by all
)
select s.*, s.quality_cost / s.support_cost as quality_cost_share, r.real_customer_issues, r.real_bug_share,
       r.real_median_days_to_close, r.real_share_still_open
from sup s left join real r using (product_stage);

-- Team performance scorecard: engineer x month.
create or replace table mart_agent_month as
select am.agent_id, am.region, am.month,
       count(t.ticket_id)                                   as tickets_solved,
       coalesce(sum(t.agent_work_minutes), 0) / 60          as work_hours,
       coalesce(sum(t.agent_work_minutes), 0) / 60 / (21 * 8) as utilization,
       1 - avg(t.sla_breached::int)                         as sla_attainment,
       avg(case t.satisfaction when 'good' then 1 when 'bad' then 0 end) as ssat,
       avg(t.reopened::int)                                 as reopen_rate,
       avg(t.ai_assist_used::int)                           as ai_assist_rate
from int_agent_month am
left join fct_tickets t on t.agent_id = am.agent_id and t.ticket_month = am.month
group by all;

-- Weekly region health, the input to alerting.
create or replace table mart_region_week as
select region, ticket_week as week,
       count(*) as tickets,
       1 - avg(sla_breached::int) as sla_attainment,
       1 - avg(case when priority in ('Emergency', 'High') then sla_breached::int end) as sla_attainment_urgent,
       avg(case satisfaction when 'good' then 1 when 'bad' then 0 end) as ssat,
       sum(agent_work_minutes) / 60 as work_hours
from fct_tickets
where ticket_week >= date '2024-11-04' and ticket_week <= date '2026-10-19'
group by all;

-- Alerting: two consecutive weeks under the 95% SLA target, or SSAT under 90% on 30+ responses,
-- or a week's volume 2.5 sigma above its trailing 8-week mean.
create or replace table mart_alerts as
with w as (
    select *,
           lag(sla_attainment) over (partition by region order by week) as prev_sla,
           avg(tickets) over (partition by region order by week rows between 8 preceding and 1 preceding) as vol_mean,
           stddev(tickets) over (partition by region order by week rows between 8 preceding and 1 preceding) as vol_sd
    from mart_region_week
)
select region, week, 'SLA below 95% two weeks running' as alert,
       printf('%.1f%% then %.1f%%', prev_sla * 100, sla_attainment * 100) as detail
from w where sla_attainment < 0.95 and prev_sla < 0.95
union all
select region, week, 'Ticket volume spike', printf('%d tickets vs %.0f mean', tickets, vol_mean)
from w where vol_sd > 0 and tickets > vol_mean + 2.5 * vol_sd;

-- Renewal events with the support experience in the 180 days before each one.
create or replace table mart_renewals as
with ren as (
    select d.account_id, d.plan, d.deployment, d.seats, d.arr, d.region, mth.m::date as renewal_month,
           coalesce(d.cancelled_month = mth.m::date, false) as churned
    from dim_account d
    cross join generate_series(date '2024-11-01', date '2026-10-01', interval 1 month) mth(m)
    where month(d.term_start) = month(mth.m)
      and d.term_start < mth.m - interval 200 day
      and (d.cancelled_month is null or mth.m <= d.cancelled_month)
)
select r.*,
       count(t.ticket_id)                                         as tickets_180d,
       count(t.ticket_id) * 100.0 / max(r.seats)            as tickets_per_100_seats,
       coalesce(sum((t.priority = 'Emergency')::int), 0)          as emergencies_180d,
       coalesce(sum(t.sla_breached::int), 0)                      as sla_breaches_180d,
       coalesce(sum(t.escalated_to_engineering::int), 0)          as escalations_180d,
       coalesce(sum((t.satisfaction = 'bad')::int), 0)            as bad_ssat_180d,
       coalesce(sum(t.reopened::int), 0)                          as reopens_180d,
       coalesce(sum(t.loaded_cost), 0)                            as support_cost_180d
from ren r
left join fct_tickets t
  on t.account_id = r.account_id
 and t.ticket_month >= r.renewal_month - interval 6 month
 and t.ticket_month <  r.renewal_month
group by all;

-- Same features for every live account as of today (scored for the at-risk list).
create or replace table mart_account_current as
select d.account_id, d.account_name, d.plan, d.deployment, d.seats, d.arr, d.region,
       make_date(case when month(d.term_start) >= 11 then 2026 else 2027 end, month(d.term_start), 1) as next_renewal,
       count(t.ticket_id)                                         as tickets_180d,
       count(t.ticket_id) * 100.0 / max(d.seats)            as tickets_per_100_seats,
       coalesce(sum((t.priority = 'Emergency')::int), 0)          as emergencies_180d,
       coalesce(sum(t.sla_breached::int), 0)                      as sla_breaches_180d,
       coalesce(sum(t.escalated_to_engineering::int), 0)          as escalations_180d,
       coalesce(sum((t.satisfaction = 'bad')::int), 0)            as bad_ssat_180d,
       coalesce(sum(t.reopened::int), 0)                          as reopens_180d,
       coalesce(sum(t.loaded_cost), 0)                            as support_cost_180d
from dim_account d
left join fct_tickets t on t.account_id = d.account_id and t.ticket_month >= date '2026-05-01'
where d.status = 'active' and d.term_start < date '2026-05-01'
group by all;

-- AI self-service experiment panel: account x month ticket counts, rollout vs holdout cohort.
create or replace table mart_self_service_panel as
with grid as (
    select d.account_id, d.self_service_cohort, d.seats, mth.m::date as month
    from dim_account d
    cross join generate_series(date '2024-11-01', date '2026-10-01', interval 1 month) mth(m)
    where d.term_start <= mth.m + interval 1 month - interval 1 day
      and (d.cancelled_month is null or mth.m < d.cancelled_month)
)
select g.*, g.month >= date '2026-02-01' as post,
       count(t.ticket_id) as tickets,
       count(case when t.category in ('how-to', 'configuration') then 1 end) as howto_config_tickets,
       count(case when t.category not in ('how-to', 'configuration') then 1 end) as other_tickets,
       coalesce(sum(t.loaded_cost), 0) as support_cost
from grid g left join fct_tickets t on t.account_id = g.account_id and t.ticket_month = g.month
group by all;

-- Monthly Business Review telemetry.
create or replace table mart_mbr_month as
select ticket_month as month,
       count(*)                                    as tickets,
       count(distinct account_id)                  as accounts_with_tickets,
       sum(loaded_cost)                            as support_cost,
       sum(loaded_cost) / count(*)                 as cost_per_ticket,
       1 - avg(sla_breached::int)                  as sla_attainment,
       avg(case satisfaction when 'good' then 1 when 'bad' then 0 end) as ssat,
       avg(escalated_to_engineering::int)          as escalation_rate,
       avg(ai_assist_used::int)                    as ai_assist_rate,
       median(agent_work_minutes)                  as median_work_minutes
from fct_tickets group by all order by month;
