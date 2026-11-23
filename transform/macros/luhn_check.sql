{% macro luhn_check(npi) -%}
{#-
    True when npi is a 10-digit NPI with a valid check digit; false for
    anything else, including null, so the result is never null and never
    an error (safe in where clauses and boolean columns).

    CMS defines the NPI check digit as the Luhn check digit of the prefix
    80840 followed by the NPI's first nine digits. Counting from the left,
    digits 1, 3, 5, 7 and 9 are doubled (a doubled value above 9 has 9
    subtracted), the prefix contributes a constant 24, and the total
    including digit 10 must be a multiple of 10.

    try_cast rather than cast: SQL engines may evaluate the arithmetic
    before the shape checks, and a cast of a letter would raise.

    Portable as written: substr, try_cast, translate and % behave the same
    on DuckDB and Snowflake, so there is no adapter dispatch.
-#}
coalesce(
    length({{ npi }}) = 10
    and translate({{ npi }}, '0123456789', '') = ''
    and (
        24
        {%- for position in range(1, 11) %}
        {%- set digit = "try_cast(substr(" ~ npi ~ ", " ~ position ~ ", 1) as integer)" %}
        {%- if position is odd %}
        + case when {{ digit }} < 5 then 2 * {{ digit }} else 2 * {{ digit }} - 9 end
        {%- else %}
        + {{ digit }}
        {%- endif %}
        {%- endfor %}
    ) % 10 = 0,
    false
)
{%- endmacro %}
