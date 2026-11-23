{% test valid_npi(model, column_name) %}
{#-
    Rows whose NPI fails the CMS check digit (macros/luhn_check.sql). Nulls
    are not_null's concern and are skipped. Used with severity warn in
    staging: an invalid NPI is reported on every build, and plan S29 flags
    the row instead of dropping it or stopping the pipeline.
-#}
select {{ column_name }}
from {{ model }}
where {{ column_name }} is not null
    and not {{ luhn_check(column_name) }}
{% endtest %}
