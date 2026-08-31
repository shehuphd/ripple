"""SQLAlchemy models for the accepted screenplay, graph, and change history.

The full schema, with kinded assertion endpoints:
both ends of an assertion are explicitly kinded, so a scene can be a graph
node. Entity attributes and the model-call audit record ride alongside.

Portability notes. UUIDs use `sqlalchemy.Uuid`, which is a native UUID on
PostgreSQL and a 32-character string on SQLite. Enumerated values are stored as
strings with CHECK constraints rather than native database enums, because
altering a PostgreSQL enum is a migration and altering a CHECK is not, and
SQLite has no enum type at all. The lists are the closed vocabularies, which is
authoritative; changing one here without changing the lock is a defect.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Declarative base for every Ripple table."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    """A stable primary key generated in Python, not by the database.

    Generating client-side lets the change-set service build a graph of new
    rows and their references before any of them is committed.
    """
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _now() -> datetime:
    """Timezone-aware creation timestamp."""
    return datetime.now(UTC)


def _in(column: str, values: tuple[str, ...]) -> CheckConstraint:
    """A CHECK restricting a column to a fixed vocabulary."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"ck_{column}_vocabulary")


# The closed vocabularies.
UNIT_TYPES = (
    "scene_heading",
    "action",
    "character",
    "dialogue",
    "parenthetical",
    "transition",
    "shot",
    "note",
)
PARSER_METHODS = (
    "fountain",
    "fdx",
    "pdf_layout",
    "rule",
    "ocr",
    "agent_repair",
    "user_corrected",
)
ENTITY_TYPES = (
    "cast",
    "prop",
    "wardrobe",
    "location",
    "set_design",
    "makeup",
    "transportation",
    "vfx",
    "stunt",
    "sound",
)
PREDICATES = (
    "appears_in",
    "occurs_at",
    "wears",
    "carries",
    "uses",
    "requires",
    "travels_by",
    "interacts_with",
    "establishes",
)
NODE_KINDS = ("entity", "scene")
PROVENANCE = ("model", "user", "accepted_change", "system")
IMPORT_OUTCOMES = ("accepted", "accepted_with_warnings", "needs_review", "rejected")
RUN_STATUSES = ("pending", "running", "partially_ready", "ready", "failed", "cancelled")
SCENE_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
CHANGE_KINDS = ("edit", "multi_unit_edit", "undo", "direct_save")
CHANGE_STATUSES = ("pending", "accepted", "rejected", "stale", "reverted", "failed")
OPERATION_TYPES = (
    "add_assertion",
    "remove_assertion",
    "update_assertion",
    "create_entity",
    "update_entity",
    "set_entity_attribute",
    "remove_entity_attribute",
    "set_unit_text",
)
FINDING_STATUSES = ("open", "dismissed", "resolved")
GRAPH_STATUSES = ("not_analysed", "analysing", "partially_ready", "ready", "failed")
MODEL_CALL_PURPOSES = ("extract", "judge", "continuity", "synthesize", "query")
MODEL_CALL_OUTCOMES = (
    "ok",
    # The reply was replayed from an identical earlier call at zero cost.
    "cached",
    "provider_error",
    "malformed",
    "truncated",
    # Schema-valid, but verdicts for listed items were missing.
    "incomplete",
    "budget_refused",
)


class Script(Base):
    """One screenplay. `current_version` guards atomic acceptance."""

    __tablename__ = "scripts"
    __table_args__ = (
        _in("import_status", IMPORT_OUTCOMES),
        _in("graph_status", GRAPH_STATUSES),
        CheckConstraint("current_version >= 0", name="ck_script_version_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(500))
    import_status: Mapped[str] = mapped_column(String(32))
    graph_status: Mapped[str] = mapped_column(String(32), default="not_analysed")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    # When the reader last displayed this script. Distinct from updated_at,
    # which moves on any write: a script edited by an accepted change was not
    # thereby opened, and "Recently opened" means what the user looked at.
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    imports: Mapped[list[Import]] = relationship(
        back_populates="script", cascade="all, delete-orphan"
    )
    scenes: Mapped[list[Scene]] = relationship(
        back_populates="script",
        cascade="all, delete-orphan",
        order_by="Scene.sequence_index",
    )
    entities: Mapped[list[Entity]] = relationship(
        back_populates="script", cascade="all, delete-orphan"
    )


class Import(Base):
    """Provenance for one upload: what it was and how it was read."""

    __tablename__ = "imports"
    __table_args__ = (_in("outcome", IMPORT_OUTCOMES),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), index=True
    )
    detected_format: Mapped[str] = mapped_column(String(32))
    adapter_name: Mapped[str] = mapped_column(String(32))
    source_name: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    warnings_json: Mapped[list | None] = mapped_column(JSON, default=list)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    script: Mapped[Script] = relationship(back_populates="imports")


class Scene(Base):
    """One scene. `display_scene_number` is presentation, never identity."""

    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("script_id", "sequence_index", name="uq_scene_order"),
        CheckConstraint("sequence_index >= 0", name="ck_scene_index_non_negative"),
        CheckConstraint("current_version >= 0", name="ck_scene_version_non_negative"),
        Index("ix_scenes_script_order", "script_id", "sequence_index"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE")
    )
    display_scene_number: Mapped[str | None] = mapped_column(String(16))
    sequence_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text)
    int_ext: Mapped[str | None] = mapped_column(String(16))
    time_of_day: Mapped[str | None] = mapped_column(String(32))
    current_version: Mapped[int] = mapped_column(Integer, default=1)

    script: Mapped[Script] = relationship(back_populates="scenes")
    units: Mapped[list[ScriptUnit]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        order_by="ScriptUnit.sequence_index",
    )


class ScriptUnit(Base):
    """The atomic editable object."""

    __tablename__ = "script_units"
    __table_args__ = (
        UniqueConstraint("scene_id", "sequence_index", name="uq_unit_order"),
        _in("unit_type", UNIT_TYPES),
        _in("parser_method", PARSER_METHODS),
        CheckConstraint("sequence_index >= 0", name="ck_unit_index_non_negative"),
        CheckConstraint("current_version >= 0", name="ck_unit_version_non_negative"),
        CheckConstraint(
            "parser_confidence >= 0 AND parser_confidence <= 1",
            name="ck_unit_confidence_range",
        ),
        Index("ix_units_scene_order", "scene_id", "sequence_index"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE")
    )
    unit_type: Mapped[str] = mapped_column(String(32))
    sequence_index: Mapped[int] = mapped_column(Integer)
    speaker_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL")
    )
    # The cue text the parser read, before any entity exists to resolve it to.
    # Extraction turns this into speaker_entity_id; keeping it means a reload
    # between import and extraction does not lose who was speaking.
    speaker_name: Mapped[str | None] = mapped_column(String(300))
    current_text: Mapped[str] = mapped_column(Text)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    parser_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    parser_method: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    scene: Mapped[Scene] = relationship(back_populates="units")
    anchors: Mapped[list[SourceAnchor]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )


class SourceAnchor(Base):
    """Immutable provenance into the imported document.

    Nullable throughout because availability depends on the adapter. A unit the
    user created has no anchor at all.
    """

    __tablename__ = "source_anchors"
    __table_args__ = (
        CheckConstraint(
            "source_page_number IS NULL OR source_page_number >= 1",
            name="ck_anchor_page_positive",
        ),
        CheckConstraint(
            "source_start_offset IS NULL OR source_start_offset >= 0",
            name="ck_anchor_start_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("script_units.id", ondelete="CASCADE"), index=True
    )
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imports.id", ondelete="SET NULL")
    )
    source_page_number: Mapped[int | None] = mapped_column(Integer)
    source_block_index: Mapped[int | None] = mapped_column(Integer)
    source_start_offset: Mapped[int | None] = mapped_column(Integer)
    source_end_offset: Mapped[int | None] = mapped_column(Integer)
    bounding_box_json: Mapped[dict | None] = mapped_column(JSON)
    extraction_method: Mapped[str] = mapped_column(String(32))

    unit: Mapped[ScriptUnit] = relationship(back_populates="anchors")


class Entity(Base):
    """A canonical production entity, unique per script and type."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint(
            "script_id", "entity_type", "normalized_name", name="uq_entity_canonical"
        ),
        _in("entity_type", ENTITY_TYPES),
        Index("ix_entities_lookup", "script_id", "entity_type", "normalized_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE")
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    canonical_name: Mapped[str] = mapped_column(String(300))
    # Stored rather than computed: a unique index on a Python function is not
    # portable between PostgreSQL and SQLite. Written via naming.normalize.
    normalized_name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    script: Mapped[Script] = relationship(back_populates="entities")
    aliases: Mapped[list[EntityAlias]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    attributes: Mapped[list[EntityAttribute]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class EntityAlias(Base):
    """A surface form resolving to one entity."""

    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "normalized_alias", name="uq_alias_per_entity"),
        Index("ix_aliases_normalized", "normalized_alias"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    alias: Mapped[str] = mapped_column(String(300))
    normalized_alias: Mapped[str] = mapped_column(String(300))
    provenance: Mapped[str] = mapped_column(String(32), default="model")

    entity: Mapped[Entity] = relationship(back_populates="aliases")


class EntityAttribute(Base):
    """An evidence-backed key-value fact about one entity.

    Descriptors leave the name: "the emerald gown" is entity `gown` with
    `color: emerald`, so an attribute edit is one changed row naming the key
    and both values, never a coincidence of entity naming. The schema
    section 5. One active row per (entity, key); a new value is an update
    operation, and history lives in change operations, as with assertions.
    """

    __tablename__ = "entity_attributes"
    __table_args__ = (
        _in("provenance", PROVENANCE),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_attribute_confidence_range",
        ),
        CheckConstraint(
            "evidence_start IS NULL OR evidence_start >= 0",
            name="ck_attribute_evidence_non_negative",
        ),
        # A model must cite its evidence; a user may state a fact directly.
        CheckConstraint(
            "provenance != 'model' OR source_unit_id IS NOT NULL",
            name="ck_attribute_model_needs_evidence",
        ),
        # Partial on `active`, matching the assertion dedupe index: deactivating
        # a value frees the key for its replacement without losing history.
        Index(
            "uq_attribute_active_key",
            "entity_id",
            "key",
            unique=True,
            sqlite_where=text("active = 1"),
            postgresql_where=text("active"),
        ),
        Index("ix_attributes_entity", "entity_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    # Normalized token via naming.normalize_key: NFKC, casefold, spaces to
    # underscores. The display form is the value; the key is vocabulary.
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(300))
    source_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_units.id", ondelete="CASCADE")
    )
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[str] = mapped_column(String(32), default="model")
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    entity: Mapped[Entity] = relationship(back_populates="attributes")


class Assertion(Base):
    """An evidence-backed directed edge between two graph nodes.

    Both ends are kinded, so a scene can be a subject or an object. The schema
    v1 section 1 explains why, and section 4 fixes which predicate accepts which
    kind. The CHECKs below enforce only the structural half of that: precisely
    one foreign key per side, agreeing with its kind. Predicate signatures are
    validated in application code, where a violation can name the predicate.
    """

    __tablename__ = "assertions"
    __table_args__ = (
        _in("predicate", PREDICATES),
        _in("provenance", PROVENANCE),
        _in("subject_kind", NODE_KINDS),
        _in("object_kind", NODE_KINDS),
        CheckConstraint(
            "(subject_kind = 'entity' AND subject_entity_id IS NOT NULL "
            "AND subject_scene_id IS NULL) OR "
            "(subject_kind = 'scene' AND subject_scene_id IS NOT NULL "
            "AND subject_entity_id IS NULL)",
            name="ck_assertion_subject_side",
        ),
        CheckConstraint(
            "(object_kind = 'entity' AND object_entity_id IS NOT NULL "
            "AND object_scene_id IS NULL) OR "
            "(object_kind = 'scene' AND object_scene_id IS NOT NULL "
            "AND object_entity_id IS NULL)",
            name="ck_assertion_object_side",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_assertion_confidence_range"
        ),
        CheckConstraint(
            "evidence_start IS NULL OR evidence_start >= 0",
            name="ck_assertion_evidence_non_negative",
        ),
        # Re-extraction must not duplicate an edge from the same evidence.
        #
        # The index is on a single derived column rather than on the seven
        # identity columns, because a SQL unique index treats NULLs as
        # distinct and every assertion has a NULL on each side by
        # construction: an entity subject leaves subject_scene_id null. A
        # multi-column index would therefore never collide and would silently
        # permit the duplicates it exists to prevent.
        #
        # Partial on `active`, so history-preserving deactivation frees the key.
        Index(
            "uq_assertion_active_dedupe",
            "dedupe_key",
            unique=True,
            sqlite_where=text("active = 1"),
            postgresql_where=text("active"),
        ),
        Index("ix_assertions_subject", "subject_entity_id", "predicate"),
        Index("ix_assertions_object", "object_entity_id", "predicate"),
        Index("ix_assertions_source_unit", "source_unit_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(16))
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    subject_scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE")
    )
    predicate: Mapped[str] = mapped_column(String(32))
    object_kind: Mapped[str] = mapped_column(String(16))
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE")
    )
    object_scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE")
    )
    source_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("script_units.id", ondelete="CASCADE")
    )
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="SET NULL")
    )
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    provenance: Mapped[str] = mapped_column(String(32), default="model")
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Derived, never set by callers. See the dedupe index above.
    dedupe_key: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def compute_dedupe_key(self) -> str:
        """The identity of this edge: both endpoints, the predicate, the evidence."""
        subject = self.subject_entity_id or self.subject_scene_id
        obj = self.object_entity_id or self.object_scene_id
        return "|".join(
            [
                self.subject_kind or "",
                str(subject),
                self.predicate or "",
                self.object_kind or "",
                str(obj),
                str(self.source_unit_id),
            ]
        )


@event.listens_for(Assertion, "before_insert")
@event.listens_for(Assertion, "before_update")
def _refresh_dedupe_key(_mapper, _connection, target: Assertion) -> None:
    """Keep the derived key correct however the row was built."""
    target.dedupe_key = target.compute_dedupe_key()


class ExtractionRun(Base):
    """One whole-script graph build."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        _in("status", RUN_STATUSES),
        CheckConstraint("total_scenes >= 0", name="ck_run_total_non_negative"),
        CheckConstraint("completed_scenes >= 0", name="ck_run_completed_non_negative"),
        CheckConstraint("failed_scenes >= 0", name="ck_run_failed_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    prompt_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(120))
    total_scenes: Mapped[int] = mapped_column(Integer, default=0)
    completed_scenes: Mapped[int] = mapped_column(Integer, default=0)
    failed_scenes: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SceneExtraction(Base):
    """Durable per-scene state, so extraction resumes rather than restarts."""

    __tablename__ = "scene_extractions"
    __table_args__ = (
        UniqueConstraint(
            "scene_id",
            "input_hash",
            "prompt_version",
            "model_id",
            name="uq_scene_extraction_cache_key",
        ),
        _in("status", SCENE_STATUSES),
        CheckConstraint("attempt_count >= 0", name="ck_scene_attempts_non_negative"),
        Index("ix_scene_extractions_run_status", "extraction_run_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="CASCADE")
    )
    scene_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    input_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(120))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChangeSet(Base):
    """A proposed or applied change. Rejected and reverted stay distinct."""

    __tablename__ = "change_sets"
    __table_args__ = (
        _in("kind", CHANGE_KINDS),
        _in("status", CHANGE_STATUSES),
        CheckConstraint(
            "base_script_version >= 0", name="ck_change_set_base_non_negative"
        ),
        Index("ix_change_sets_script_status", "script_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE")
    )
    reverts_change_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("change_sets.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    base_script_version: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    units: Mapped[list[ChangeSetUnit]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan"
    )
    operations: Mapped[list[ChangeOperation]] = relationship(
        back_populates="change_set",
        cascade="all, delete-orphan",
        order_by="ChangeOperation.sequence_index",
    )
    findings: Mapped[list[ContinuityFinding]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan"
    )


class ChangeSetUnit(Base):
    """A unit a change set edits, with the version it was based on."""

    __tablename__ = "change_set_units"
    __table_args__ = (
        UniqueConstraint("change_set_id", "script_unit_id", name="uq_change_set_unit"),
        CheckConstraint("base_unit_version >= 0", name="ck_csu_base_non_negative"),
        Index("ix_change_set_units_unit", "script_unit_id", "change_set_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    script_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("script_units.id", ondelete="CASCADE")
    )
    base_unit_version: Mapped[int] = mapped_column(Integer)
    proposed_text: Mapped[str] = mapped_column(Text)

    change_set: Mapped[ChangeSet] = relationship(back_populates="units")


class ChangeOperation(Base):
    """One ordered, invertible operation.

    `target_id` is nullable because a proposed entity has no UUID until the
    change set is accepted. `before_json` and `after_json` hold snapshots
    sufficient to validate and invert the operation.
    """

    __tablename__ = "change_operations"
    __table_args__ = (
        UniqueConstraint(
            "change_set_id", "sequence_index", name="uq_change_operation_order"
        ),
        _in("operation_type", OPERATION_TYPES),
        CheckConstraint("sequence_index >= 0", name="ck_operation_index_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    sequence_index: Mapped[int] = mapped_column(Integer)
    operation_type: Mapped[str] = mapped_column(String(32))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)

    change_set: Mapped[ChangeSet] = relationship(back_populates="operations")


class ContinuityFinding(Base):
    """An evidence-backed warning. Dismissal records a reason and fixes nothing."""

    __tablename__ = "continuity_findings"
    __table_args__ = (
        _in("status", FINDING_STATUSES),
        Index("ix_findings_change_set_status", "change_set_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    finding_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    dismissal_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    change_set: Mapped[ChangeSet] = relationship(back_populates="findings")
    evidence: Mapped[list[FindingEvidence]] = relationship(
        back_populates="finding",
        cascade="all, delete-orphan",
        order_by="FindingEvidence.rank",
    )


class FindingEvidence(Base):
    """A unit or assertion a finding cites."""

    __tablename__ = "finding_evidence"
    __table_args__ = (
        CheckConstraint("rank >= 0", name="ck_evidence_rank_non_negative"),
        Index("ix_finding_evidence_unit", "script_unit_id"),
        Index("ix_finding_evidence_assertion", "assertion_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("continuity_findings.id", ondelete="CASCADE")
    )
    script_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_units.id", ondelete="CASCADE")
    )
    assertion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assertions.id", ondelete="CASCADE")
    )
    rank: Mapped[int] = mapped_column(Integer, default=0)
    match_reason: Mapped[str] = mapped_column(String(64))

    finding: Mapped[ContinuityFinding] = relationship(back_populates="evidence")


class RippleReport(Base):
    """One report per change set, replaced when a preview is regenerated."""

    __tablename__ = "ripple_reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


class QueryLog(Base):
    """Audit record for grounded questions. Never a source of graph truth."""

    __tablename__ = "query_log"
    __table_args__ = (Index("ix_query_log_script_time", "script_id", "asked_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE")
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    cited_assertion_ids_json: Mapped[list | None] = mapped_column(JSON, default=list)
    model_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelCall(Base):
    """One model interaction, recorded in full for the audit surface.

    Distinct from TraceAct's redacted operational traces: this row exists so
    the user can audit what the model was asked and answered, so it carries
    the full prompt and raw response. Application data, deleted with its
    script. A `budget_refused` row records a call the budget cap prevented,
    with zero tokens, so the ledger shows what the cap saved.
    """

    __tablename__ = "model_calls"
    __table_args__ = (
        _in("purpose", MODEL_CALL_PURPOSES),
        _in("outcome", MODEL_CALL_OUTCOMES),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_call_input_tokens_non_negative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_call_output_tokens_non_negative",
        ),
        CheckConstraint("duration_ms >= 0", name="ck_call_duration_non_negative"),
        Index("ix_model_calls_script_time", "script_id", "created_at"),
        Index("ix_model_calls_change_set", "change_set_id"),
        Index("ix_model_calls_purpose", "purpose", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE")
    )
    change_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("change_sets.id", ondelete="SET NULL")
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL")
    )
    purpose: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(120))
    request_text: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(16))
    error_message: Mapped[str | None] = mapped_column(Text)
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BudgetSetting(Base):
    """The user's token budget. One row under key "default"; no row, no cap.

    The cap is compared against the sum of recorded token counts in
    `model_calls`, so it can only be as complete as the audit table. Every
    call site writes its row for that reason, not only for the Traces page.
    """

    __tablename__ = "budget_settings"
    __table_args__ = (
        CheckConstraint(
            "max_total_tokens > 0", name="ck_budget_positive"
        ),
    )

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    max_total_tokens: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class UiPreference(Base):
    """One user-set interface preference. No row means the default applies."""

    __tablename__ = "ui_preferences"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class AppConfiguration(Base):
    """Selected provider and model identifiers. Never credentials.

    Survives every script and graph deletion. Google credentials live only in
    Replit Secrets.
    """

    __tablename__ = "app_configuration"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
