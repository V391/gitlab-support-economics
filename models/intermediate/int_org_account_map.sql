-- Unified customer schema, step 1: map every Zendesk organization to exactly one CRM account.
-- Three match rules, strongest first. Each org keeps the rule that matched it, so match quality is reportable.

create or replace table int_org_account_map as
with candidates as (
    -- 1. Zendesk org carries the Salesforce id
    select o.zendesk_org_id, o.sfdc_account_id, 1 as rule_rank, 'zendesk_sfdc_id' as match_rule
    from stg_zendesk_orgs o where o.sfdc_account_id is not null
    union all
    -- 2. Salesforce account carries the Zendesk org id
    select c.zendesk_org_id, c.sfdc_account_id, 2, 'crm_zendesk_org_id'
    from stg_crm_accounts c where c.zendesk_org_id is not null
    union all
    -- 3. Email domain equals the CRM website domain
    select o.zendesk_org_id, c.sfdc_account_id, 3, 'domain'
    from stg_zendesk_orgs o join stg_crm_accounts c on o.domain = c.domain
)
select zendesk_org_id, sfdc_account_id, match_rule
from candidates
qualify row_number() over (partition by zendesk_org_id order by rule_rank) = 1;
