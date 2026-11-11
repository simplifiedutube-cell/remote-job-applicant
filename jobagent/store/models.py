"""SQLAlchemy ORM -- the single source of truth for the database schema.

Chosen over hand-written SQL migration files because this is the only place the
`JobStatus` enum, the CHECK constraints, and the Python state machine can share one
definition; separate SQL files drift from the Python within a month. Kept honest by
`tests/pg/test_alembic_drift.py`, which asserts `alembic revision --autogenerate`
produces an empty diff.

Everything lives in the `jobagent` schema. LangGraph's checkpoint tables live in the
`langgraph` schema in the same database, created by `PostgresSaver.setup()` and
explicitly excluded from Alembic autogenerate (see `migrations/env.py`) -- without that
exclusion, autogenerate emits DROP TABLE for them.

Three constraints in this file do the real safety work:

* `applications_one_live_submit_uidx` -- a partial unique index that makes a second live
  submit for the same job a `UniqueViolation`. Independent of `RetryPolicy`, of whether a
  node remembered to check, and it survives a mid-run kill and resume.
* `ck_applications_applied_needs_evidence` -- guardrail 4 restated in SQL, so it holds
  even against a hand-written INSERT.
* `jobs_source_id_uidx` -- catches same-portal reposts with a cosmetically edited title.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from jobagent.graph.state import ApplyRoute, JobStatus

SCHEMA = "jobagent"
CHECKPOINT_SCHEMA = "langgraph"

#: 384 = BAAI/bge-small-en-v1.5. Baked into the DDL; changing embedding model is a
#: migration, which is why every vector row also records `embedding_model`.
EMBED_DIM = 384

# Deterministic constraint names. Without this, Alembic autogenerate produces spurious
# diffs for unnamed constraints and `test_alembic_drift` fails for no real reason.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


def _enum(python_enum: type, name: str) -> Enum:
    """Native PG enum keyed on `.value`, not `.name` (SQLAlchemy defaults to `.name`)."""
    return Enum(
        python_enum,
        name=name,
        schema=SCHEMA,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


_TZ = DateTime(timezone=True)
_NOW = func.now()
_FP = String(64)

#: For NULLABLE jsonb columns only. By default SQLAlchemy encodes Python `None` as the
#: JSON literal `null`, which is *not* SQL NULL -- so `coalesce(excluded.col, table.col)`
#: in an upsert happily selects the JSON null and silently discards the previous value.
#: `none_as_null=True` makes None mean SQL NULL, which is what the merge logic assumes.
#: Purely a Python-side flag; the DDL is unchanged.
_NULLABLE_JSONB = JSONB(none_as_null=True)


def _fingerprint_fk(*, nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[str]:
    return mapped_column(
        _FP, ForeignKey(f"{SCHEMA}.jobs.fingerprint", ondelete=ondelete), nullable=nullable
    )


# --------------------------------------------------------------------------- runs


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    #: LangGraph thread id -- the join key to the checkpoint tables in the other schema.
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    profile_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: Guardrail 3, recorded per run so an audit can tell dry from live after the fact.
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(
        Enum(
            "running",
            "completed",
            "failed",
            "interrupted",
            name="run_status",
            schema=SCHEMA,
        ),
        nullable=False,
        server_default=text("'running'"),
    )
    search_specs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ)

    __table_args__ = (
        CheckConstraint(
            "(status IN ('completed','failed') AND finished_at IS NOT NULL) "
            "OR (status IN ('running','interrupted') AND finished_at IS NULL)",
            name="finished_iff_terminal",
        ),
        Index("ix_runs_started_at_desc", text("started_at DESC")),
    )


# --------------------------------------------------------------------------- jobs


class Job(Base):
    """`fingerprint` is the primary key: dedupe is structural, not a query."""

    __tablename__ = "jobs"

    fingerprint: Mapped[str] = mapped_column(_FP, primary_key=True)

    company: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Remote'"))
    # Persisted so any fingerprint is auditable and reproducible without re-running
    # normalize.py at the version it was computed with.
    norm_company: Mapped[str] = mapped_column(Text, nullable=False)
    norm_title: Mapped[str] = mapped_column(Text, nullable=False)
    norm_location: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    apply_url: Mapped[str | None] = mapped_column(Text)
    ats_vendor: Mapped[str | None] = mapped_column(Text)

    remote_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    employment_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    comp: Mapped[dict[str, Any] | None] = mapped_column(_NULLABLE_JSONB)
    timezone_hint: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), nullable=False, server_default=text("'discovered'")
    )

    posted_at: Mapped[datetime | None] = mapped_column(_TZ)
    first_seen_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)
    last_seen_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)
    first_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.runs.run_id"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_order"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_is_sha256_hex"),
        # A repost under the same portal id with a cosmetically edited title would
        # otherwise slip past the fingerprint.
        Index(
            "jobs_source_id_uidx",
            "source",
            "source_job_id",
            unique=True,
            postgresql_where=text("source_job_id IS NOT NULL"),
        ),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_last_seen_at_desc", text("last_seen_at DESC")),
        Index(
            "ix_jobs_norm_company_trgm",
            "norm_company",
            postgresql_using="gin",
            postgresql_ops={"norm_company": "gin_trgm_ops"},
        ),
    )


class JobStatusEvent(Base):
    """Append-only audit of every status change.

    Not in the original spec -- added because "screened-out jobs are logged with a
    reason, never silently dropped" needs somewhere to put the reason, and because a
    status machine without an audit trail is unfalsifiable after the fact. Written by the
    database trigger, so a caller cannot forget it.
    """

    __tablename__ = "job_status_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = _fingerprint_fk()
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"))
    from_status: Mapped[JobStatus | None] = mapped_column(_enum(JobStatus, "job_status"))
    to_status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), nullable=False)
    node: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        Index("ix_job_status_events_fingerprint_occurred", "fingerprint", "occurred_at"),
    )


# --------------------------------------------------------------------------- screening


class FitVerdictRow(Base):
    """One screening verdict. Below-threshold jobs are recorded here, never dropped."""

    __tablename__ = "fit_verdicts"

    verdict_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = _fingerprint_fk()
    run_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"), nullable=False)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    blocking_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    hard_filters_failed: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    model: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        # Mirrors FitVerdict._blocked_implies_low_score.
        CheckConstraint(
            "cardinality(blocking_reasons) = 0 OR score < 70", name="blocked_implies_low_score"
        ),
        Index("ix_fit_verdicts_fingerprint", "fingerprint"),
    )


# --------------------------------------------------------------------------- applying


class Application(Base):
    __tablename__ = "applications"

    application_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = _fingerprint_fk(ondelete="RESTRICT")
    run_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"), nullable=False)
    route: Mapped[ApplyRoute] = mapped_column(_enum(ApplyRoute, "apply_route"), nullable=False)
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)

    submitted_at: Mapped[datetime | None] = mapped_column(_TZ)
    final_url: Mapped[str | None] = mapped_column(Text)
    confirmation_text: Mapped[str | None] = mapped_column(Text)
    evidence_screenshot: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        CheckConstraint(
            "result_status IN ('applied','uncertain','failed','skipped_dry_run')",
            name="result_status_enum",
        ),
        CheckConstraint("attempts >= 1", name="attempts_positive"),
        # Guardrail 4, in SQL as well as in Pydantic.
        CheckConstraint(
            "result_status <> 'applied' "
            "OR (evidence_screenshot IS NOT NULL "
            "    AND (confirmation_text IS NOT NULL OR final_url IS NOT NULL))",
            name="applied_needs_evidence",
        ),
        # A dry run must never be able to masquerade as a real submission.
        CheckConstraint(
            "NOT (dry_run AND result_status = 'applied')", name="dry_run_is_never_applied"
        ),
        # GUARDRAIL 2. The single most important line in this schema: at most one live
        # submit per job, ever. The `dry_run = false` predicate is what still permits
        # dry-running the same job a hundred times.
        Index(
            "applications_one_live_submit_uidx",
            "fingerprint",
            unique=True,
            postgresql_where=text("dry_run = false AND result_status IN ('applied','uncertain')"),
        ),
        Index("ix_applications_run_id", "run_id"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str | None] = mapped_column(
        _FP, ForeignKey(f"{SCHEMA}.jobs.fingerprint", ondelete="SET NULL")
    )
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    profile_version: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('resume_docx','resume_pdf','cover_letter','screenshot','page_html')",
            name="kind_enum",
        ),
        CheckConstraint("bytes >= 0", name="bytes_non_negative"),
        UniqueConstraint("sha256", "kind", name="uq_artifacts_sha256_kind"),
        Index("ix_artifacts_fingerprint_kind", "fingerprint", "kind"),
    )


# --------------------------------------------------------------------------- humans


class Escalation(Base):
    __tablename__ = "escalations"

    escalation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(
        _FP, ForeignKey(f"{SCHEMA}.jobs.fingerprint", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    #: Thread to resume once the human answers the `interrupt()`.
    thread_id: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)
    resolved_at: Mapped[datetime | None] = mapped_column(_TZ)

    __table_args__ = (
        CheckConstraint("(answer IS NULL) = (resolved_at IS NULL)", name="answer_iff_resolved"),
        Index(
            "ix_escalations_open",
            "created_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )


class ManualQueue(Base):
    """Dead-letter queue. Guardrail: nothing silently vanishes."""

    __tablename__ = "manual_queue"

    fingerprint: Mapped[str] = mapped_column(
        _FP, ForeignKey(f"{SCHEMA}.jobs.fingerprint", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    queued_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)
    cleared_at: Mapped[datetime | None] = mapped_column(_TZ)


class ErrorRow(Base):
    __tablename__ = "errors"

    error_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey(f"{SCHEMA}.runs.run_id"), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(_FP)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    error_type: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    occurred_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        CheckConstraint("severity IN ('warning','error','fatal')", name="severity_enum"),
        Index("ix_errors_run_id_occurred", "run_id", "occurred_at"),
    )


# --------------------------------------------------------------------------- vectors


class ProfileEvidence(Base):
    """M4: embedded bullets from master_profile.yaml -- the only source of resume claims."""

    __tablename__ = "profile_evidence"

    evidence_id: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "kns.bullet.3"
    profile_version: Mapped[str] = mapped_column(Text, primary_key=True)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    employer: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Machine-checkable structure {tool, years, metric, employer} for verify_claims.
    facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        CheckConstraint(
            "section IN ('summary','experience','project','skill','education')",
            name="section_enum",
        ),
        Index(
            "profile_evidence_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class JobEmbedding(Base):
    """M2: repost detection at cosine similarity > 0.92."""

    __tablename__ = "job_embeddings"

    fingerprint: Mapped[str] = mapped_column(
        _FP, ForeignKey(f"{SCHEMA}.jobs.fingerprint", ondelete="CASCADE"), primary_key=True
    )
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=_NOW)

    __table_args__ = (
        Index(
            "job_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --------------------------------------------------------------------------- comp typing
# Declared last so `Decimal`/`Numeric`/`Integer` stay imported for the comp JSONB shape
# documented here rather than modelled as columns:
#   comp = {"min_amount": Decimal, "max_amount": Decimal, "currency": "USD",
#           "period": "year"}
# Stored as JSONB because portals report comp in wildly inconsistent shapes and M3's hard
# filters are the only consumer; promoting it to columns is an M3 decision, not an M1 one.
_COMP_JSONB_DOC = (Decimal, Numeric, Integer)
