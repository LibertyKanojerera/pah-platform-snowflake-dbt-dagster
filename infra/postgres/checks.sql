-- every prescriber resolves against the CMS set
SELECT count(DISTINCT prescriber_npi) FROM patients;

-- censoring rate: want roughly 30-45%, not 5% and not 80%
SELECT count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM patient_events e
         WHERE e.patient_id = p.patient_id AND e.event_type = 'discontinuation'
       ))::float / count(*) AS censored_rate
FROM patients p;

-- the signal should be visible before any model is fitted
SELECT initial_route, copay_tier, count(*), round(avg(months)::numeric, 1)
FROM (
  SELECT p.patient_id, p.initial_route, p.copay_tier,
         (max(e.event_timestamp)::date - p.index_date) / 30.4375 AS months
  FROM patients p JOIN patient_events e USING (patient_id)
  GROUP BY 1, 2, 3
) t GROUP BY 1, 2 ORDER BY 1, 2;
