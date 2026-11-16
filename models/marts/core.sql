-- Core marts: unified account, fully costed ticket fact, monthly support cost.

-- One row per customer account: CRM identity + billing contract + Zendesk footprint.
create or replace table dim_account as
select
    c.sfdc_account_id                     as account_id,
    c.account_name,
    c.region,
    s.plan,
    s.deployment,
    s.seats,
    s.arr,
    s.term_start,
    s.status,
    s.cancelled_month,
    count(m.zendesk_org_id)               as zendesk_org_count,
    min(e.self_service_cohort)            as self_service_cohort
from stg_crm_accounts c
join stg_subscriptions s using (sfdc_account_id)
left join int_org_account_map m using (sfdc_account_id)
left join stg_experiment e on e.zendesk_org_id = m.zendesk_org_id and e.zendesk_org_id < '900000'
group by all;

-- Calendar of active engineers per region and month (paid capacity).
create or replace table int_agent_month as
select a.agent_id, a.region, a.cost_per_hour, mth.m::date as month
from stg_agents a
cross join generate_series(date '2024-11-01', date '2026-10-01', interval 1 month) mth(m)
where a.hire_date <= mth.m and (a.leave_date is null or a.leave_date > mth.m);

-- Paid cost vs ticket work per region-month. The overhead factor spreads non-ticket time
-- (meetings, training, internal work, idle cover) onto tickets so cost per ticket reconciles to payroll.
create or replace table mart_support_cost_month as
with paid as (
    select region, month, count(*) as engineers,
           count(*) * 21 * 8 as paid_hours,
           sum(cost_per_hour * 21 * 8) as paid_cost
    from int_agent_month group by all
), work as (
    select a.region, t.ticket_month as month, count(*) as tickets,
           sum(t.agent_work_minutes) / 60 as work_hours,
           sum(t.agent_work_minutes / 60 * a.cost_per_hour) as direct_cost
    from stg_tickets t join stg_agents a using (agent_id)
    group by all
)
select p.*, w.tickets, w.work_hours, w.direct_cost,
       w.work_hours / p.paid_hours as utilization,
       p.paid_cost / w.direct_cost as overhead_factor
from paid p join work w using (region, month);

-- Ticket fact: every ticket with its account, engineer and fully loaded cost.
create or replace table fct_tickets as
select
    t.*,
    m.sfdc_account_id                  as account_id,
    m.match_rule,
    d.plan, d.deployment, d.seats, d.arr, d.self_service_cohort,
    a.region,
    t.agent_work_minutes / 60 * a.cost_per_hour                         as direct_cost,
    t.agent_work_minutes / 60 * a.cost_per_hour * c.overhead_factor     as loaded_cost,
    t.ticket_month >= date '2026-02-01'                                 as self_service_live_period,
    t.ticket_month >= case a.region when 'EMEA' then date '2026-03-01'
                                    when 'AMER' then date '2026-06-01'
                                    else date '2026-09-01' end           as assist_available
from stg_tickets t
join int_org_account_map m using (zendesk_org_id)
join dim_account d on d.account_id = m.sfdc_account_id
join stg_agents a using (agent_id)
join mart_support_cost_month c on c.region = a.region and c.month = t.ticket_month;
