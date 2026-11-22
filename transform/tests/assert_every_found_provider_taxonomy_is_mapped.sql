-- Every taxonomy code carried by a provider NPPES found must be mapped. There
-- is no default category: a new code fails here and gets a deliberate
-- mapping. The specialty version of the ingest sweep (ADR-015).
select distinct nppes.taxonomy_code, nppes.taxonomy_desc
from {{ source('landing', 'nppes') }} as nppes
left join {{ ref('specialty_category_map') }} as mapping
    on nppes.taxonomy_code = mapping.taxonomy_code
where nppes.nppes_found
    and nppes.taxonomy_code <> ''
    and mapping.taxonomy_code is null
