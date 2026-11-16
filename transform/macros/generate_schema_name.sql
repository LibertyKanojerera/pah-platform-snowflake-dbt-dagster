{#
    Schema routing. On the target named prod, a model's custom schema is used
    as written: staging, marts, snapshots, seeds. On every other target,
    everything goes into that target's own schema, so a dev build can never
    overwrite what consumers read. Only the target that builds what Streamlit
    and Power BI read may be named prod.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ generate_schema_name_for_env(custom_schema_name, node) }}
{%- endmacro %}
