"""Tests for the MAIA Second Brain V1 storage/governance foundation.

Every test uses an isolated temporary database (``tmp_path``) -- none
of these tests touch ``~/.openjarvis`` or any production data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.second_brain.types import (
    EntryTrustStatus,
    EntryType,
    EvidenceReference,
    RelationshipStatus,
    RelationshipType,
    Visibility,
)


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_second_brain.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


# -- A/B/C: basic entry creation across types ------------------------------


def test_a_create_event(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.EVENT,
        title="Fermo linea M",
        summary="Fermo macchina rilevato sulla linea M.",
        created_by="user:luigi",
        provenance="Segnalato durante il turno mattutino.",
        source="conversation",
        trust_status=EntryTrustStatus.OBSERVED,
        domains=["production"],
        entities=["linea M"],
    )
    assert entry.id
    assert entry.type is EntryType.EVENT
    assert entry.trust_status is EntryTrustStatus.OBSERVED
    assert service.get_entry(entry.id, actor="user:luigi") is not None


def test_b_create_problem(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.PROBLEM,
        title="OEE in calo",
        summary="OEE sceso rispetto al mese precedente.",
        created_by="user:luigi",
        provenance="Osservato confrontando i dati di produzione.",
        source="conversation",
        trust_status=EntryTrustStatus.OBSERVED,
        domains=["production"],
    )
    assert entry.type is EntryType.PROBLEM


def test_c_create_hypothesis(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.HYPOTHESIS,
        title="Ipotesi: cambio formato",
        summary="Il fermo potrebbe essere causato dal cambio formato.",
        created_by="user:luigi",
        provenance="Ipotesi formulata dopo l'evento di fermo.",
        source="conversation",
        trust_status=EntryTrustStatus.HYPOTHESIS,
        domains=["production"],
    )
    assert entry.trust_status is EntryTrustStatus.HYPOTHESIS


# -- D/E: governance rejections --------------------------------------------


def test_d_reject_invalid_confidence(service: SecondBrainService):
    with pytest.raises(SecondBrainValidationError):
        service.create_entry(
            type=EntryType.OBSERVATION,
            title="x",
            summary="x",
            created_by="user:luigi",
            provenance="x",
            source="conversation",
            trust_status=EntryTrustStatus.OBSERVED,
            confidence=1.5,
        )


def test_e_reject_missing_created_by(service: SecondBrainService):
    with pytest.raises(SecondBrainValidationError):
        service.create_entry(
            type=EntryType.OBSERVATION,
            title="x",
            summary="x",
            created_by="",
            provenance="x",
            source="conversation",
            trust_status=EntryTrustStatus.OBSERVED,
        )


# -- F: no auto-promotion ---------------------------------------------------


def test_f_hypothesis_does_not_auto_promote(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.HYPOTHESIS,
        title="Ipotesi",
        summary="Ipotesi da verificare.",
        created_by="user:luigi",
        provenance="Ipotesi iniziale.",
        source="conversation",
        trust_status=EntryTrustStatus.HYPOTHESIS,
    )
    # No API exists to mutate trust_status on an existing entry --
    # re-reading it must show the exact same status, unchanged by time
    # or by any other call the service exposes.
    reread = service.get_entry(entry.id, actor="user:luigi")
    assert reread is not None
    assert reread.trust_status is EntryTrustStatus.HYPOTHESIS
    assert not hasattr(service, "promote_entry")
    assert not hasattr(service, "update_entry")


# -- G/H: DECISION and OUTCOME ----------------------------------------------


def test_g_create_decision_with_provenance(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.DECISION,
        title="Decisione: fermare la linea per manutenzione",
        summary="Decisione presa dopo la verifica del cambio formato.",
        created_by="user:luigi",
        provenance="Basata sull'ipotesi verificata sul cambio formato.",
        source="conversation",
        trust_status=EntryTrustStatus.DECISION,
        timestamp=1700000000.0,
    )
    assert entry.timestamp == 1700000000.0


def test_g_decision_requires_timestamp(service: SecondBrainService):
    with pytest.raises(SecondBrainValidationError):
        service.create_entry(
            type=EntryType.DECISION,
            title="Decisione senza timestamp",
            summary="x",
            created_by="user:luigi",
            provenance="x",
            source="conversation",
            trust_status=EntryTrustStatus.DECISION,
        )


def test_h_create_outcome(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.OUTCOME,
        title="Esito: fermo risolto",
        summary="La manutenzione ha risolto il fermo.",
        created_by="user:luigi",
        provenance="Verificato il turno successivo.",
        source="conversation",
        trust_status=EntryTrustStatus.OUTCOME,
        domains=["production"],
    )
    assert entry.type is EntryType.OUTCOME


# -- I/J: LESSON grounding ---------------------------------------------------


def test_i_create_lesson_linked_to_outcome(service: SecondBrainService):
    outcome = service.create_entry(
        type=EntryType.OUTCOME,
        title="Esito: fermo risolto",
        summary="La manutenzione ha risolto il fermo.",
        created_by="user:luigi",
        provenance="Verificato il turno successivo.",
        source="conversation",
        trust_status=EntryTrustStatus.OUTCOME,
        domains=["production"],
    )
    lesson = service.create_entry(
        type=EntryType.LESSON,
        title="Lezione: controllare il cambio formato",
        summary="In caso di fermo, verificare prima il cambio formato.",
        created_by="user:luigi",
        provenance="Derivata dall'esito della manutenzione sulla linea M.",
        source="conversation",
        trust_status=EntryTrustStatus.LEARNED,
        domains=["production"],
        entities=["linea M"],
    )
    rel = service.create_relationship(
        source_entry_id=lesson.id,
        target_entry_id=outcome.id,
        relation_type=RelationshipType.RESULTED_IN,
        source="conversation",
        created_by="user:luigi",
    )
    assert rel.status is RelationshipStatus.PROPOSED
    assert lesson.trust_status is EntryTrustStatus.LEARNED


def test_j_reject_learned_lesson_without_grounding(service: SecondBrainService):
    with pytest.raises(SecondBrainValidationError):
        service.create_entry(
            type=EntryType.LESSON,
            title="Lezione senza fondamento",
            summary="x",
            created_by="user:luigi",
            provenance="x",
            source="conversation",
            trust_status=EntryTrustStatus.LEARNED,
            # no domains, no entities, no evidence_references
        )


# -- K/L: relationship PROPOSED -> CONFIRMED ---------------------------------


def test_k_create_proposed_ai_relationship(service: SecondBrainService):
    a = service.create_entry(
        type=EntryType.EVENT, title="A", summary="A", created_by="agent:orchestrator",
        provenance="A", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    b = service.create_entry(
        type=EntryType.EVENT, title="B", summary="B", created_by="agent:orchestrator",
        provenance="B", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    rel = service.create_relationship(
        source_entry_id=a.id,
        target_entry_id=b.id,
        relation_type=RelationshipType.CORRELATES_WITH,
        source="agent:orchestrator",
        created_by="agent:orchestrator",
        confidence=0.6,
    )
    assert rel.status is RelationshipStatus.PROPOSED
    # No parameter exists to force a different status at creation.
    import inspect

    sig = inspect.signature(service.create_relationship)
    assert "status" not in sig.parameters


def test_l_confirm_relationship_explicitly(service: SecondBrainService):
    a = service.create_entry(
        type=EntryType.EVENT, title="A", summary="A", created_by="agent:orchestrator",
        provenance="A", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    b = service.create_entry(
        type=EntryType.EVENT, title="B", summary="B", created_by="agent:orchestrator",
        provenance="B", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    rel = service.create_relationship(
        source_entry_id=a.id, target_entry_id=b.id,
        relation_type=RelationshipType.RELATED_TO,
        source="agent:orchestrator", created_by="agent:orchestrator",
    )
    confirmed = service.update_relationship_status(
        rel.id, RelationshipStatus.CONFIRMED, actor="user:luigi"
    )
    assert confirmed.status is RelationshipStatus.CONFIRMED


# -- M: supersede without destroying history ---------------------------------


def test_m_supersede_entry_preserves_old_version(service: SecondBrainService):
    old = service.create_entry(
        type=EntryType.OBSERVATION,
        title="Osservazione iniziale",
        summary="Testo originale, poi corretto.",
        created_by="user:luigi",
        provenance="Prima osservazione.",
        source="conversation",
        trust_status=EntryTrustStatus.OBSERVED,
    )
    updated_old, rel = service.supersede_entry(
        old.id,
        actor="user:luigi",
        new_entry_kwargs=dict(
            type=EntryType.OBSERVATION,
            title="Osservazione corretta",
            summary="Testo corretto.",
            created_by="user:luigi",
            provenance="Correzione della precedente osservazione.",
            source="conversation",
            trust_status=EntryTrustStatus.VERIFIED,
        ),
    )
    assert updated_old.id == old.id
    assert updated_old.title == "Osservazione iniziale"  # original content untouched
    assert updated_old.summary == "Testo originale, poi corretto."
    assert updated_old.superseded_by is not None
    new_entry = service.get_entry(updated_old.superseded_by, actor="user:luigi")
    assert new_entry is not None
    assert new_entry.title == "Osservazione corretta"
    assert rel.relation_type is RelationshipType.SUPERSEDES
    assert rel.status is RelationshipStatus.CONFIRMED


# -- N: archive ----------------------------------------------------------


def test_n_archive_entry(service: SecondBrainService):
    entry = service.create_entry(
        type=EntryType.EVENT, title="Da archiviare", summary="x", created_by="user:luigi",
        provenance="x", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    assert entry.archived_at is None
    archived = service.archive_entry(entry.id, actor="user:luigi")
    assert archived.archived_at is not None
    # archived entries are excluded from default listing
    listed = service.list_entries(actor="user:luigi")
    assert all(e.id != entry.id for e in listed)
    listed_incl = service.list_entries(actor="user:luigi", include_archived=True)
    assert any(e.id == entry.id for e in listed_incl)


# -- O/P: retrieval -------------------------------------------------------


def test_o_fts_search(service: SecondBrainService):
    service.create_entry(
        type=EntryType.EVENT, title="Fermo linea M", summary="Cambio formato sulla linea M",
        created_by="user:luigi", provenance="x", source="conversation",
        trust_status=EntryTrustStatus.OBSERVED, domains=["production"],
    )
    service.create_entry(
        type=EntryType.EVENT, title="Ritardo consegna", summary="Ritardo fornitore logistica",
        created_by="user:luigi", provenance="x", source="conversation",
        trust_status=EntryTrustStatus.OBSERVED, domains=["logistics"],
    )
    results = service.search_entries("cambio formato", actor="user:luigi")
    assert len(results) == 1
    assert results[0].title == "Fermo linea M"


def test_o_fts_search_excludes_archived_entries(service: SecondBrainService):
    """FASE 4N.4 bugfix, found live: an archived entry still appeared in
    FTS results (list_entries() correctly excluded it, search_entries_fts()
    never checked archived_at at all)."""
    entry = service.create_entry(
        type=EntryType.EVENT, title="Fermo linea N", summary="Cambio formato sulla linea N",
        created_by="user:luigi", provenance="x", source="conversation",
        trust_status=EntryTrustStatus.OBSERVED, domains=["production"],
    )
    service.archive_entry(entry.id, actor="user:luigi")
    results = service.search_entries("cambio formato", actor="user:luigi")
    assert results == []


def test_o_fts_search_handles_fts5_special_characters(service: SecondBrainService):
    """FASE 4N.2A regression: a bareword like 'Zeta-9' used to crash FTS5
    with 'no such column: 9' (found live, Claude Sonnet 4.6 hit this on
    a real query) -- unescaped user text let FTS5's query grammar
    misparse plain content as syntax."""
    service.create_entry(
        type=EntryType.PROBLEM, title="Problema linea Zeta-9",
        summary="Calibrazione sensori sulla linea Zeta-9",
        created_by="user:luigi", provenance="x", source="conversation",
        trust_status=EntryTrustStatus.OBSERVED, domains=["production"],
    )
    for query in ("Zeta-9", "calibrazione sensori Zeta-9", 'title:hack OR 1=1', "NOT AND OR"):
        results = service.search_entries(query, actor="user:luigi")
        assert isinstance(results, list)  # must not raise
    results = service.search_entries("Zeta-9", actor="user:luigi")
    assert len(results) == 1
    assert results[0].title == "Problema linea Zeta-9"


def test_p_domain_entity_filtering(service: SecondBrainService):
    service.create_entry(
        type=EntryType.EVENT, title="A", summary="A", created_by="user:luigi",
        provenance="x", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
        domains=["production"], entities=["linea M"],
    )
    service.create_entry(
        type=EntryType.EVENT, title="B", summary="B", created_by="user:luigi",
        provenance="x", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
        domains=["logistics"], entities=["fornitore X"],
    )
    prod = service.list_entries(actor="user:luigi", domain="production")
    assert len(prod) == 1
    assert prod[0].title == "A"
    by_entity = service.list_entries(actor="user:luigi", entity="fornitore X")
    assert len(by_entity) == 1
    assert by_entity[0].title == "B"


# -- Q: evidence reference never stores KPI value ----------------------------


def test_q_evidence_reference_no_kpi_value(service: SecondBrainService):
    ref = EvidenceReference(
        capability="ops.production.get_kpi",
        domain="production",
        metric="oee",
        period="2026-07",
        filters={"line": "M"},
        trust_status_at_capture="TRUSTED",
        fetched_at=1700000000.0,
    )
    assert not hasattr(ref, "value")
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(ref)}
    assert "value" not in field_names
    assert "kpi_value" not in field_names

    entry = service.create_entry(
        type=EntryType.OBSERVATION,
        title="Osservazione con evidenza",
        summary="OEE osservato tramite capability certificata.",
        created_by="user:luigi",
        provenance="Collegata alla capability ops.production.get_kpi.",
        source="conversation",
        trust_status=EntryTrustStatus.VERIFIED,
        evidence_references=[ref],
    )
    reread = service.get_entry(entry.id, actor="user:luigi")
    assert reread is not None
    assert len(reread.evidence_references) == 1
    stored_ref = reread.evidence_references[0]
    assert stored_ref.capability == "ops.production.get_kpi"
    assert stored_ref.period == "2026-07"
    assert not hasattr(stored_ref, "value")


# -- R: audit hash chain integrity -------------------------------------------


def test_r_audit_hash_chain_integrity(service: SecondBrainService):
    e1 = service.create_entry(
        type=EntryType.EVENT, title="A", summary="A", created_by="user:luigi",
        provenance="x", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    e2 = service.create_entry(
        type=EntryType.EVENT, title="B", summary="B", created_by="user:luigi",
        provenance="x", source="conversation", trust_status=EntryTrustStatus.OBSERVED,
    )
    service.create_relationship(
        source_entry_id=e1.id, target_entry_id=e2.id,
        relation_type=RelationshipType.RELATED_TO,
        source="user:luigi", created_by="user:luigi",
    )
    service.archive_entry(e1.id, actor="user:luigi")

    valid, broken_id = service.verify_audit_chain()
    assert valid is True
    assert broken_id is None

    # Tamper directly at the storage layer and confirm detection.
    store = service._store  # test-only introspection
    store._conn.execute(
        "UPDATE audit_log SET action = 'TAMPERED' WHERE id = 1"
    )
    store._conn.commit()
    valid2, broken_id2 = service.verify_audit_chain()
    assert valid2 is False
    assert broken_id2 is not None
