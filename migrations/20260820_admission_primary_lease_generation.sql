ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS lease_generation BIGINT NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_operational_primary_device
  ON admission_operational_devices(operational_session_id)
  WHERE station_role='PRIMARY' AND detached_at IS NULL;
