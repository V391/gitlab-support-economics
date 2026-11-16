-- Each test is a query that returns failing rows. One block per test, each opened by a test header line.

-- test: ticket_id is unique
select ticket_id, count(*) from stg_tickets group by 1 having count(*) > 1;

-- test: every Zendesk org maps to exactly one account
select o.zendesk_org_id from stg_zendesk_orgs o
left join int_org_account_map m using (zendesk_org_id)
group by 1 having count(m.sfdc_account_id) <> 1;

-- test: no ticket lost in the account join
select (select count(*) from stg_tickets) - (select count(*) from fct_tickets) as lost
where (select count(*) from stg_tickets) <> (select count(*) from fct_tickets);

-- test: every account has a billing contract and a region
select account_id from dim_account where plan is null or region is null;

-- test: SLA target matches the priority's first reply target
select ticket_id from stg_tickets
where sla_target_minutes <> case priority when 'Emergency' then 30 when 'High' then 240
                                          when 'Medium' then 480 else 1440 end;

-- test: engineer was employed when the ticket was worked
select t.ticket_id from fct_tickets t
left join int_agent_month am on am.agent_id = t.agent_id and am.month = t.ticket_month
where am.agent_id is null;

-- test: no negative or missing work time
select ticket_id from stg_tickets where agent_work_minutes is null or agent_work_minutes <= 0;

-- test: loaded ticket cost reconciles to payroll within 0.1% every region-month
select c.region, c.month, c.paid_cost, sum(t.loaded_cost) as allocated
from mart_support_cost_month c join fct_tickets t on t.region = c.region and t.ticket_month = c.month
group by all having abs(sum(t.loaded_cost) / c.paid_cost - 1) > 0.001;

-- test: no tickets after an account cancelled
select t.ticket_id from fct_tickets t join dim_account d using (account_id)
where d.cancelled_month is not null and t.ticket_month >= d.cancelled_month;

-- test: every account sits in exactly one experiment cohort
select account_id from dim_account where self_service_cohort is null;
