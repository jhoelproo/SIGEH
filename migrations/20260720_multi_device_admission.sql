BEGIN;

ALTER TABLE admission_attention_projection
    ADD COLUMN IF NOT EXISTS turn_id BIGINT;

ALTER TABLE active_sessions
    DROP CONSTRAINT IF EXISTS active_sessions_pkey;

ALTER TABLE active_sessions
    ADD CONSTRAINT active_sessions_pkey PRIMARY KEY (session_id);

CREATE INDEX IF NOT EXISTS idx_active_sessions_user_active
    ON active_sessions(username, is_active, last_seen);

CREATE INDEX IF NOT EXISTS idx_admission_projection_current_turn
    ON admission_attention_projection(source_instance_id, turn_id, readiness);

COMMIT;
