-- luhn_check against NPIs whose answer is known: CMS's worked example
-- (1234567893), hand-computed check digits including a check digit of 0,
-- and every way an NPI can be wrong. Returns the cases the macro gets
-- wrong, so it passes when it returns nothing. Reads no data, so it needs
-- no AWS credentials and runs unchanged on Snowflake.
--
-- The columns are read through the VALUES alias (v.npi) on purpose: the
-- AL05 autofix deletes an alias it thinks is unused, column list
-- included, and the columns silently become col0, col1 and col2.

with cases as (
    select
        v.npi,
        v.expected,
        v.reason
    from (
        values
        ('1234567893', true, 'CMS worked example'),
        ('1000000004', true, 'one doubled digit'),
        ('1999999992', true, 'doubled digits of 9'),
        ('2876543218', true, 'starts with 2'),
        ('1000000020', true, 'check digit 0'),
        ('1234567890', false, 'wrong check digit'),
        ('1234567894', false, 'check digit off by one'),
        ('1243567893', false, 'adjacent digits transposed'),
        ('123456789', false, 'nine digits'),
        ('12345678931', false, 'eleven digits'),
        ('12345678A3', false, 'a letter'),
        (' 234567893', false, 'a leading space'),
        ('', false, 'blank'),
        (null, false, 'null')
    ) as v (npi, expected, reason)
)

select
    npi,
    expected,
    reason,
    {{ luhn_check('npi') }} as actual
from cases
where {{ luhn_check('npi') }} <> expected
