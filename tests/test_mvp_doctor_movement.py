"""Tests for the MVP Doctor MOVEMENT-category validator (check_cmc_slide_config).

Punch-list #5 (roadmap-coverage scan 2026-05-30): MOVEMENT was a zero-check category,
so a movement-topic diagnose() could only ever fail-closed. This registers a real static
CMC slide-config validator. Grounded against the real SWCharacterMovementComponent.h
Sworder|Slide block (SlideGroundFriction, SlideMaxDuration).

Fully offline — pure file read, no bridge, no UE5.
"""
from __future__ import annotations

from pathlib import Path

from core.mvp_doctor import (
    _CHECK_REGISTRY,
    Category,
    MVPDoctor,
    Severity,
)

_VALID_HEADER = """\
// SWCharacterMovementComponent.h (test stub)
UCLASS()
class USWCharacterMovementComponent : public UCharacterMovementComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Sworder|Slide")
    float SlideImpulseStrength = 1200.0f;

    UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Sworder|Slide")
    float SlideMinSpeed = 150.0f;

    UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Sworder|Slide")
    float SlideGroundFriction = 0.5f;

    UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Sworder|Slide")
    float BrakingDecelerationSliding = 512.0f;

    UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Sworder|Slide")
    float SlideMaxDuration = 1.5f;
};
"""


def _make_project(tmp_path: Path, header_text: str | None) -> Path:
    """Build a minimal UE5 project tree with (optionally) the CMC header."""
    cmc_dir = tmp_path / "Source" / "MyProject" / "Variant_Combat"
    cmc_dir.mkdir(parents=True, exist_ok=True)
    if header_text is not None:
        (cmc_dir / "SWCharacterMovementComponent.h").write_text(header_text, encoding="utf-8")
    # Content dir so other checks don't explode if diagnose() is run whole
    (tmp_path / "Content").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestMovementCheckRegistered:
    def test_movement_category_now_has_a_check(self):
        movement_checks = [name for name, cat, _ in _CHECK_REGISTRY if cat == Category.MOVEMENT]
        assert movement_checks, "MOVEMENT must have at least one registered @check"
        assert "CMC Slide Config" in movement_checks


class TestCMCSlideConfig:
    def test_passes_valid_header(self, tmp_path):
        proj = _make_project(tmp_path, _VALID_HEADER)
        doctor = MVPDoctor(ue5_project_path=str(proj))
        findings = doctor.check_cmc_slide_config()
        assert findings == []

    def test_flags_missing_friction(self, tmp_path):
        header = _VALID_HEADER.replace("    float SlideGroundFriction = 0.5f;\n", "")
        proj = _make_project(tmp_path, header)
        doctor = MVPDoctor(ue5_project_path=str(proj))
        findings = doctor.check_cmc_slide_config()
        ids = {f.id for f in findings}
        assert "CMC_SLIDE_FRICTION_MISSING" in ids
        assert all(f.severity == Severity.MEDIUM for f in findings if f.id == "CMC_SLIDE_FRICTION_MISSING")

    def test_flags_zero_friction(self, tmp_path):
        header = _VALID_HEADER.replace("SlideGroundFriction = 0.5f", "SlideGroundFriction = 0.0f")
        proj = _make_project(tmp_path, header)
        doctor = MVPDoctor(ue5_project_path=str(proj))
        findings = doctor.check_cmc_slide_config()
        zero = [f for f in findings if f.id == "CMC_SLIDE_FRICTION_ZERO"]
        assert zero, "zero SlideGroundFriction must be flagged"
        assert zero[0].severity == Severity.HIGH

    def test_flags_zero_maxduration(self, tmp_path):
        header = _VALID_HEADER.replace("SlideMaxDuration = 1.5f", "SlideMaxDuration = 0.0f")
        proj = _make_project(tmp_path, header)
        doctor = MVPDoctor(ue5_project_path=str(proj))
        findings = doctor.check_cmc_slide_config()
        ids = {f.id for f in findings}
        assert "CMC_SLIDE_MAXDURATION_ZERO" in ids

    def test_missing_header(self, tmp_path):
        proj = _make_project(tmp_path, None)  # no .h written
        doctor = MVPDoctor(ue5_project_path=str(proj))
        findings = doctor.check_cmc_slide_config()
        assert any(f.id == "CMC_H_MISSING" for f in findings)


class TestMovementDiagnoseIntegration:
    def test_diagnose_movement_runs_and_does_not_failclosed(self, tmp_path):
        """A MOVEMENT-targeted diagnose() now runs a real check (>=1) and a valid header
        produces NO NO_VALIDATOR fail-closed finding."""
        proj = _make_project(tmp_path, _VALID_HEADER)
        diag = MVPDoctor(ue5_project_path=str(proj)).diagnose(categories=[Category.MOVEMENT])
        assert diag.checks_run >= 1
        assert not any(f.id == "NO_VALIDATOR_FOR_REQUESTED_CATEGORIES" for f in diag.findings)
        assert diag.is_demo_ready  # valid header -> no blocking findings
