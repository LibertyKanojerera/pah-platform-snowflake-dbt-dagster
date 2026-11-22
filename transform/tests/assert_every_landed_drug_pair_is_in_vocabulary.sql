-- The warehouse twin of the ingest sweep (ADR-015). The sweep refuses unknown
-- pairs at landing; this catches the other direction, a row removed from the
-- vocabulary after its data landed, which would silently lose route and
-- product type downstream.
select distinct src.Gnrc_Name, src.Brnd_Name
from {{ source('landing', 'cms_part_d') }} as src
left join {{ ref('drug_vocabulary') }} as voc
    on src.Gnrc_Name = voc.gnrc_name
    and src.Brnd_Name = voc.brnd_name
where voc.brnd_name is null
