"""Recover admission authors only from durable creation evidence."""


def repair_admission_authorship(connection):
    result = connection.execute(
        """WITH originals AS (
            SELECT DISTINCT ON (entity_uuid) entity_uuid,
                   payload_json->>'admission_username' AS admission_username
            FROM admission_sync_events
            WHERE entity_type='attention' AND operation='CREATE'
            ORDER BY entity_uuid,sequence
        )
        UPDATE admission_attention_projection p
        SET admission_username=o.admission_username,
            latest_payload_json=COALESCE(p.latest_payload_json,'{}'::jsonb)
                || jsonb_build_object('admission_username',o.admission_username)
        FROM originals o
        WHERE p.global_attention_id=o.entity_uuid
          AND NULLIF(TRIM(o.admission_username),'') IS NOT NULL
          AND p.admission_username IS DISTINCT FROM o.admission_username"""
    )
    return result.rowcount
