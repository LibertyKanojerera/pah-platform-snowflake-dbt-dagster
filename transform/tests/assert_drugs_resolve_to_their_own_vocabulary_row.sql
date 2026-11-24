-- Every drug must resolve to its own vocabulary row: the route, molecule
-- and product type it carries are the ones drug_vocabulary gives that
-- exact (generic, brand) pair. The not_null and row-count tests cannot see
-- a join that matches the wrong row: under the S28 mutation, Tyvaso Refill
-- Kit matched the Tyvaso drug row, which shares its generic string, and
-- became an inhaled drug with every count still adding up (ADR-022).

select
    resolved.generic_name,
    resolved.brand_name,
    resolved.route as resolved_route,
    vocabulary.route as vocabulary_route,
    resolved.product_type as resolved_product_type,
    vocabulary.product_type as vocabulary_product_type
from {{ ref('int_drugs__route_resolved') }} as resolved
inner join {{ ref('drug_vocabulary') }} as vocabulary
    on
        resolved.generic_name = vocabulary.gnrc_name
        and resolved.brand_name = vocabulary.brnd_name
where
    resolved.route is distinct from vocabulary.route
    or resolved.molecule is distinct from vocabulary.molecule
    or resolved.product_type is distinct from vocabulary.product_type
