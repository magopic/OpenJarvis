"""FASE 4O.2 STEP 14 -- Obsidian Projection V1 isolated test matrix.

Every test uses an isolated temporary Second Brain database AND an
isolated temporary vault directory (``tmp_path``) -- nothing here ever
touches a real Second Brain or a real Obsidian vault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.projections.obsidian import note_relative_path, slugify
from openjarvis.second_brain.projections.obsidian_sync import ObsidianProjection
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore

_A = "test-principal:alice"
_B = "test-principal:bob"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_sb.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "vault"


def _confirm(service: SecondBrainService, rel_id: str, actor: str = _A) -> None:
    service.update_relationship_status(rel_id, "CONFIRMED", actor=actor)


def _all_md_files(vault_path: Path) -> list[Path]:
    return sorted(vault_path.rglob("*.md"))


# -- A: active entry -> correct folder/note ---------------------------------


def test_a_active_entry_correct_folder(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title="Problema linea M", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED",
        domains=["test-domain"], entities=["Linea M"],
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    rel_path = note_relative_path(entry)
    assert (vault_path / rel_path).exists()
    assert "Problems" in str(rel_path)


# -- B: duplicate titles -> no collision -------------------------------------


def test_b_duplicate_titles_no_collision(service: SecondBrainService, vault_path: Path):
    e1 = service.create_entry(
        type="PROBLEM", title="Stesso titolo", summary="uno", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED",
    )
    e2 = service.create_entry(
        type="PROBLEM", title="Stesso titolo", summary="due", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    stats = proj.rebuild()
    assert stats.notes_written == 2
    p1, p2 = note_relative_path(e1), note_relative_path(e2)
    assert p1 != p2
    assert (vault_path / p1).exists()
    assert (vault_path / p2).exists()


# -- C: relationship -> correct wikilink -------------------------------------


def test_c_relationship_correct_wikilink(service: SecondBrainService, vault_path: Path):
    problem = service.create_entry(
        type="PROBLEM", title="Problema", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED",
    )
    outcome = service.create_entry(
        type="OUTCOME", title="Esito", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="OUTCOME",
    )
    rel = service.create_relationship(
        source_entry_id=outcome.id, target_entry_id=problem.id,
        relation_type="RESULTED_IN", source="conv", created_by=_A,
    )
    _confirm(service, rel.id)

    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    problem_note = (vault_path / note_relative_path(problem)).read_text(encoding="utf-8")
    outcome_filename = note_relative_path(outcome).name[:-3]
    assert f"[[{outcome_filename}]]" in problem_note
    assert "RESULTED_IN" in problem_note


# -- D: CONFIRMED vs PROPOSED visually distinct ------------------------------


def test_d_confirmed_vs_proposed_visually_distinct(service: SecondBrainService, vault_path: Path):
    a = service.create_entry(
        type="PROBLEM", title="A", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    b = service.create_entry(
        type="PROBLEM", title="B", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    c = service.create_entry(
        type="PROBLEM", title="C", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    confirmed_rel = service.create_relationship(
        source_entry_id=a.id, target_entry_id=b.id, relation_type="RELATED_TO",
        source="conv", created_by=_A,
    )
    _confirm(service, confirmed_rel.id)
    service.create_relationship(
        source_entry_id=a.id, target_entry_id=c.id, relation_type="RELATED_TO",
        source="conv", created_by=_A,
    )  # left PROPOSED

    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    note = (vault_path / note_relative_path(a)).read_text(encoding="utf-8")
    assert "CONFIRMED" in note
    assert "PROPOSED" in note
    # Distinct markers, not just the word "status" repeated identically.
    assert "✅" in note
    assert "🟡" in note


# -- E/F: PRIVATE authorization ------------------------------------------


def test_ef_private_owner_exported_other_not(service: SecondBrainService, vault_path: Path):
    private_entry = service.create_entry(
        type="OBSERVATION", title="Nota privata", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED", visibility="PRIVATE",
    )
    owner_vault = vault_path / "owner"
    other_vault = vault_path / "other"

    owner_proj = ObsidianProjection(service, owner_vault, _A)
    owner_stats = owner_proj.rebuild()
    assert owner_stats.notes_written == 1
    assert (owner_vault / note_relative_path(private_entry)).exists()

    other_proj = ObsidianProjection(service, other_vault, _B)
    other_stats = other_proj.rebuild()
    assert other_stats.notes_written == 0
    assert not (other_vault / note_relative_path(private_entry)).exists()
    # And no file anywhere in the other principal's vault mentions the
    # private entry's id/title -- not just "the specific path is missing".
    for md_file in _all_md_files(other_vault):
        content = md_file.read_text(encoding="utf-8")
        assert private_entry.id not in content
        assert "Nota privata" not in content


def test_f_unresolved_principal_fails_closed(service: SecondBrainService, vault_path: Path):
    with pytest.raises(ValueError):
        ObsidianProjection(service, vault_path, "")
    with pytest.raises(ValueError):
        ObsidianProjection(service, vault_path, None)  # type: ignore[arg-type]


# -- G: superseded -> historical folder + active link -----------------------


def test_g_superseded_historical_folder_and_link(service: SecondBrainService, vault_path: Path):
    old = service.create_entry(
        type="LESSON", title="Lezione vecchia", summary="incompleta", created_by=_A,
        provenance="x", source="conv", trust_status="LEARNED",
        domains=["test-domain"],
    )
    updated_old, _rel = service.supersede_entry(
        old.id, actor=_A,
        new_entry_kwargs=dict(
            type="LESSON", title="Lezione corretta", summary="completa",
            created_by=_A, provenance="correzione", source="conv",
            trust_status="LEARNED", domains=["test-domain"],
        ),
    )
    new_entry = service.get_entry(updated_old.superseded_by, actor=_A)

    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()

    old_rel_path = note_relative_path(updated_old)
    assert "_Superseded" in str(old_rel_path)
    assert (vault_path / old_rel_path).exists()

    old_note = (vault_path / old_rel_path).read_text(encoding="utf-8")
    new_filename = note_relative_path(new_entry).name[:-3]
    assert f"[[{new_filename}]]" in old_note
    assert "superseded" in old_note.lower()


# -- H: archived -> archived folder ------------------------------------------


def test_h_archived_folder(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="EVENT", title="Da archiviare", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED",
    )
    archived_entry = service.archive_entry(entry.id, actor=_A)
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    rel_path = note_relative_path(archived_entry)
    assert "_Archived" in str(rel_path)
    assert (vault_path / rel_path).exists()


# -- I: domain/entity pages -> correct backlinks -----------------------------


def test_i_entity_domain_pages_backlinks(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title="Problema", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED", domains=["test-domain"], entities=["Linea M"],
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()

    from openjarvis.second_brain.projections.obsidian import domain_page_filename, entity_page_filename

    entity_page = (vault_path / "MAIA" / "_Entities" / entity_page_filename("Linea M")).read_text(encoding="utf-8")
    domain_page = (vault_path / "MAIA" / "_Domains" / domain_page_filename("test-domain")).read_text(encoding="utf-8")
    entry_filename = note_relative_path(entry).name[:-3]
    assert f"[[{entry_filename}]]" in entity_page
    assert f"[[{entry_filename}]]" in domain_page
    assert "derived: true" in entity_page
    assert "derived: true" in domain_page
    assert "navigation" in entity_page.lower()


# -- J: rebuild twice -> identical projection --------------------------------


def test_j_rebuild_twice_identical(service: SecondBrainService, vault_path: Path):
    service.create_entry(
        type="PROBLEM", title="Problema", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED", domains=["test-domain"], entities=["Linea M"],
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    first = {p: p.read_text(encoding="utf-8") for p in _all_md_files(vault_path)}
    proj.rebuild()
    second = {p: p.read_text(encoding="utf-8") for p in _all_md_files(vault_path)}
    assert first == second


# -- K: update entry -> deterministic update, no orphan ----------------------


def test_k_title_change_no_orphan(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title="Titolo originale", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    old_path = vault_path / note_relative_path(entry)
    assert old_path.exists()

    # Simulate a title change via supersede_entry (the only governed way
    # content changes) and confirm the update path removes the stale file.
    updated_old, _rel = service.supersede_entry(
        entry.id, actor=_A,
        new_entry_kwargs=dict(
            type="PROBLEM", title="Titolo nuovo", summary="x", created_by=_A,
            provenance="correzione", source="conv", trust_status="OBSERVED",
        ),
    )
    stats = proj.update()
    assert stats.mode == "update"
    # Old entry's own path changed (moved to _Superseded) -- old PROBLEMS
    # path must no longer exist, replaced by the _Superseded one.
    assert not old_path.exists()
    new_old_path = vault_path / note_relative_path(updated_old)
    assert new_old_path.exists()


def test_k_incremental_skips_unchanged(service: SecondBrainService, vault_path: Path):
    service.create_entry(
        type="PROBLEM", title="Stabile", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    stats = proj.update()
    assert stats.mode == "update"
    assert stats.skipped_unchanged == 1
    assert stats.notes_written == 0


# -- L: special Windows filename characters ----------------------------------


def test_l_windows_special_characters_safe(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title='Problema: linea "M" / test <critico> | urgente?*',
        summary="x", created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    stats = proj.rebuild()
    assert stats.notes_written == 1
    rel_path = note_relative_path(entry)
    forbidden = set('<>:"/\\|?*')
    # Only the path separators from Path() itself are allowed; the
    # filename component must contain none of the forbidden characters.
    assert not (forbidden - {"/", "\\"}) & set(rel_path.name)
    assert (vault_path / rel_path).exists()


def test_l_reserved_windows_name_safe(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title="CON", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    assert (vault_path / note_relative_path(entry)).exists()


# -- M: Unicode/Italian titles -----------------------------------------------


def test_m_unicode_italian_titles_valid(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title="Perché è successo sulla linea più critica?",
        summary="Riassunto con caratteri à è ì ò ù.", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    stats = proj.rebuild()
    assert stats.notes_written == 1
    rel_path = note_relative_path(entry)
    content = (vault_path / rel_path).read_text(encoding="utf-8")
    assert "à è ì ò ù" in content
    assert "Perché" in content or "Perch" in rel_path.name  # accented char preserved somewhere


# -- N: evidence references -> metadata only, no KPI value duplication ------


def test_n_evidence_references_no_kpi_value(service: SecondBrainService, vault_path: Path):
    from openjarvis.second_brain.types import EvidenceReference

    ref = EvidenceReference(
        capability="ops.production.get_kpi", domain="production", metric="oee",
        period="2026-07", trust_status_at_capture="TRUSTED", fetched_at=1700000000.0,
    )
    entry = service.create_entry(
        type="OBSERVATION", title="Osservazione OEE", summary="x", created_by=_A,
        provenance="x", source="conv", trust_status="VERIFIED",
        evidence_references=[ref],
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()
    content = (vault_path / note_relative_path(entry)).read_text(encoding="utf-8")
    assert "ops.production.get_kpi" in content
    assert "period=2026-07" in content
    # No numeric KPI value anywhere -- EvidenceReference structurally has
    # no value field to copy (mirrors FASE 4N.1's own
    # test_q_evidence_reference_no_kpi_value).
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(ref)}
    assert "value" not in field_names
    assert "kpi_value" not in field_names
    assert "88.61" not in content


# -- O: empty Second Brain -> valid empty vault/dashboard --------------------


def test_o_empty_second_brain_valid_dashboard(service: SecondBrainService, vault_path: Path):
    proj = ObsidianProjection(service, vault_path, _A)
    stats = proj.rebuild()
    assert stats.notes_written == 0
    dashboard = (vault_path / "MAIA" / "Dashboard" / "Dashboard.md").read_text(encoding="utf-8")
    assert "Dashboard" in dashboard
    assert "0" in dashboard  # all counts should show 0, not error/crash


# -- P: crash/interrupted projection recoverable through rebuild ------------


def test_p_interrupted_projection_recoverable_via_rebuild(service: SecondBrainService, vault_path: Path):
    entry = service.create_entry(
        type="PROBLEM", title="Problema", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED",
    )
    proj = ObsidianProjection(service, vault_path, _A)
    proj.rebuild()

    # Simulate a corrupted/partial manifest (as if a crash happened
    # mid-write) -- update() must not propagate the corruption, it must
    # safely fall back to a full rebuild instead.
    manifest_path = vault_path / ".maia_projection_manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")

    stats = proj.update()
    assert stats.mode == "rebuild"  # fell back correctly
    assert (vault_path / note_relative_path(entry)).exists()
    # Manifest is valid again after the fallback rebuild.
    import json

    json.loads(manifest_path.read_text(encoding="utf-8"))  # must not raise
