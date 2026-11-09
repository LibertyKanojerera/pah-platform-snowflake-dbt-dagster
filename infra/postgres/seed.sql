-- Synthetic patient cohort for the PAH platform.
--
-- Two timestamps per row and they are not interchangeable:
--   event_timestamp  when the clinical event happened
--   created_at       when the row arrived in this database
--
-- Incremental extraction watermarks on created_at, never on event_timestamp.
-- A business timestamp as a watermark silently drops late-arriving records: a
-- fill recorded on Tuesday for a Friday dispense is already behind a watermark
-- set from the Friday events. See docs/adr/ADR-018.

CREATE TABLE IF NOT EXISTS patients (
    patient_id      BIGINT       PRIMARY KEY,
    prescriber_npi  VARCHAR(10)  NOT NULL,
    index_date      DATE         NOT NULL,
    initial_route   VARCHAR(16)  NOT NULL,
    copay_tier      SMALLINT     NOT NULL,
    age_band        VARCHAR(8)   NOT NULL,
    sex             CHAR(1)      NOT NULL,
    prescriber_state CHAR(2),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_events (
    event_id        BIGINT       PRIMARY KEY,
    patient_id      BIGINT       NOT NULL REFERENCES patients(patient_id),
    event_type      VARCHAR(16)  NOT NULL,
    event_timestamp TIMESTAMPTZ  NOT NULL,
    route           VARCHAR(16)  NOT NULL,
    days_supply     SMALLINT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- The extraction index. created_at first because that is the predicate the
-- incremental query filters on.
CREATE INDEX IF NOT EXISTS idx_events_created_at
    ON patient_events (created_at, event_id);

CREATE INDEX IF NOT EXISTS idx_events_patient
    ON patient_events (patient_id, event_timestamp);

CREATE INDEX IF NOT EXISTS idx_patients_created_at
    ON patients (created_at, patient_id);
