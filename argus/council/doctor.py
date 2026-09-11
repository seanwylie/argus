"""Doctor checks for council profiles (lightweight)."""

from __future__ import annotations

from argus.council.profiles import default_council_profile
from argus.council.routing import validate_council_profile
from argus.refinement.models import ArtifactType


def check_council_profiles() -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for default routed profiles."""
    errors: list[str] = []
    warnings: list[str] = []
    for at in (ArtifactType.IDEA, ArtifactType.PRODUCT_SPEC, ArtifactType.IMPLEMENTATION_PLAN):
        cp = default_council_profile(at)
        errs = validate_council_profile(cp)
        for e in errs:
            errors.append(f"council {at.value}: {e}")
        if cp.outsider_count() > 0 and cp.grounded_count() == 0:
            warnings.append(f"council {at.value}: outsider-only profile (invalid for feasibility gate)")
        if at == ArtifactType.IMPLEMENTATION_PLAN and cp.outsider_count() > 0:
            warnings.append(
                f"council {at.value}: includes outsider seats — ensure non-blocking; see docs/councils.md"
            )
    return errors, warnings
