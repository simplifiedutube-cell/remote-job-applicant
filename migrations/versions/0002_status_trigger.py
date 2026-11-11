"""status machine trigger + audit log

Enforcement layer 2 of 3 (see jobagent/store/transitions.py). This re-implements
LEGAL_TRANSITIONS in PL/pgSQL so that a status change made from psql, a one-off script,
or a future node that bypasses JobRepo is rejected too -- and so that the
job_status_events audit row cannot be forgotten by a caller.

The transition table is duplicated between Python and SQL on purpose. That duplication is
guarded by tests/pg/test_status_trigger.py, which walks LEGAL_TRANSITIONS from Python and
asserts the database agrees on every edge. A drift between the two is a test failure, not
a silent divergence.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGAL_SQL = """
CREATE OR REPLACE FUNCTION jobagent.job_status_is_legal(
    old_status jobagent.job_status,
    new_status jobagent.job_status
) RETURNS boolean AS $$
BEGIN
    IF old_status = new_status THEN
        RETURN true;  -- idempotent re-write
    END IF;
    RETURN CASE old_status
        WHEN 'discovered'   THEN new_status IN ('screened','screened_out','failed','closed')
        WHEN 'screened'     THEN new_status IN ('tailored','screened_out','manual_queue','failed','closed')
        WHEN 'tailored'     THEN new_status IN ('applied','uncertain','manual_queue','failed','closed')
        WHEN 'manual_queue' THEN new_status IN ('tailored','applied','uncertain','closed')
        WHEN 'uncertain'    THEN new_status IN ('applied','acked','failed','closed')
        WHEN 'applied'      THEN new_status IN ('acked','interview','closed')
        WHEN 'acked'        THEN new_status IN ('interview','closed')
        WHEN 'interview'    THEN new_status IN ('closed')
        WHEN 'screened_out' THEN new_status IN ('screened','closed')
        WHEN 'failed'       THEN new_status IN ('discovered','closed')
        WHEN 'closed'       THEN false
        ELSE false
    END;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""

TRIGGER_FN_SQL = """
CREATE OR REPLACE FUNCTION jobagent.enforce_job_status_transition()
RETURNS trigger AS $$
DECLARE
    v_run_id text := NULLIF(current_setting('jobagent.run_id', true), '');
    v_node   text := NULLIF(current_setting('jobagent.node',   true), '');
    v_reason text := NULLIF(current_setting('jobagent.reason', true), '');
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;

    IF NOT jobagent.job_status_is_legal(OLD.status, NEW.status) THEN
        RAISE EXCEPTION
            'illegal job status transition % -> % for fingerprint %',
            OLD.status, NEW.status, NEW.fingerprint
            USING ERRCODE = 'check_violation';
    END IF;

    -- The audit row is written here rather than by the caller so that it cannot be
    -- omitted. A status machine without an audit trail is unfalsifiable after the fact.
    INSERT INTO jobagent.job_status_events
        (fingerprint, run_id, from_status, to_status, node, reason)
    VALUES
        (NEW.fingerprint, v_run_id, OLD.status, NEW.status, v_node, v_reason);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

INSERT_FN_SQL = """
CREATE OR REPLACE FUNCTION jobagent.log_job_status_insert()
RETURNS trigger AS $$
DECLARE
    v_run_id text := NULLIF(current_setting('jobagent.run_id', true), '');
    v_node   text := NULLIF(current_setting('jobagent.node',   true), '');
BEGIN
    INSERT INTO jobagent.job_status_events
        (fingerprint, run_id, from_status, to_status, node, reason)
    VALUES
        (NEW.fingerprint, COALESCE(v_run_id, NEW.first_run_id), NULL, NEW.status, v_node,
         'discovered');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(LEGAL_SQL)
    op.execute(TRIGGER_FN_SQL)
    op.execute(INSERT_FN_SQL)
    op.execute(
        "CREATE TRIGGER trg_jobs_status_transition "
        "BEFORE UPDATE OF status ON jobagent.jobs "
        "FOR EACH ROW EXECUTE FUNCTION jobagent.enforce_job_status_transition()"
    )
    op.execute(
        "CREATE TRIGGER trg_jobs_status_insert "
        "AFTER INSERT ON jobagent.jobs "
        "FOR EACH ROW EXECUTE FUNCTION jobagent.log_job_status_insert()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_status_insert ON jobagent.jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_status_transition ON jobagent.jobs")
    op.execute("DROP FUNCTION IF EXISTS jobagent.log_job_status_insert()")
    op.execute("DROP FUNCTION IF EXISTS jobagent.enforce_job_status_transition()")
    op.execute(
        "DROP FUNCTION IF EXISTS jobagent.job_status_is_legal"
        "(jobagent.job_status, jobagent.job_status)"
    )
