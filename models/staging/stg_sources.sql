-- Staging: one view per source system, typed and renamed. No business logic here.

create or replace view stg_tickets as
select
    ticket_id::bigint                         as ticket_id,
    organization_id::varchar                  as zendesk_org_id,
    created_at::timestamp                     as created_at,
    solved_at::timestamp                      as solved_at,
    date_trunc('month', created_at::timestamp)::date as ticket_month,
    date_trunc('week', created_at::timestamp)::date  as ticket_week,
    priority,
    product_stage,
    category,
    assignee_id                               as agent_id,
    first_reply_minutes::double               as first_reply_minutes,
    sla_target_minutes::double                as sla_target_minutes,
    first_reply_minutes::double > sla_target_minutes::double as sla_breached,
    agent_work_minutes::double                as agent_work_minutes,
    ai_assist_used::boolean                   as ai_assist_used,
    escalated_to_engineering::boolean         as escalated_to_engineering,
    reopened::boolean                         as reopened,
    nullif(satisfaction, '')                  as satisfaction
from read_csv_auto('data/sources/zendesk_tickets.csv', all_varchar = true);

create or replace view stg_zendesk_orgs as
select org_id::varchar as zendesk_org_id, org_name, lower(trim(domain_names)) as domain,
       nullif(sfdc_id, '') as sfdc_account_id
from read_csv_auto('data/sources/zendesk_organizations.csv', all_varchar = true);

create or replace view stg_crm_accounts as
select sfdc_account_id, account_name, region,
       lower(regexp_replace(website, '^https?://(www\.)?', '')) as domain,
       nullif(zendesk_org_id, '') as zendesk_org_id
from read_csv_auto('data/sources/crm_accounts.csv', all_varchar = true);

create or replace view stg_subscriptions as
select subscription_id, sfdc_account_id, plan, deployment, seats::int as seats, arr::double as arr,
       term_start::date as term_start, status,
       case when cancelled_month <> '' then (cancelled_month || '-01')::date end as cancelled_month
from read_csv_auto('data/sources/billing_subscriptions.csv', all_varchar = true);

create or replace view stg_agents as
select agent_id, region, hire_date::date as hire_date, nullif(leave_date, '')::date as leave_date,
       cost_per_hour::double as cost_per_hour
from read_csv_auto('data/sources/agents.csv', all_varchar = true);

create or replace view stg_self_service as
select org_id::varchar as zendesk_org_id, (month || '-01')::date as conv_month,
       bot_marked_resolved::boolean as bot_marked_resolved
from read_csv_auto('data/sources/self_service_conversations.csv', all_varchar = true);

create or replace view stg_experiment as
select org_id::varchar as zendesk_org_id, self_service_cohort
from read_csv_auto('data/sources/experiment_assignment.csv', all_varchar = true);

create or replace view stg_real_issues as
select iid::bigint as iid, created_at::timestamp as created_at, nullif(closed_at, '')::timestamp as closed_at,
       state, nullif(stage, '') as stage, nullif("group", '') as product_group, nullif(type, '') as issue_type,
       nullif(severity, '') as severity, upvotes::int as upvotes, notes::int as notes
from read_csv_auto('data/real/gitlab_customer_issues.csv', all_varchar = true);
