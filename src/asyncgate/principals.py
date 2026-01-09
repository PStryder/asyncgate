"""Canonical principal identifiers and helpers."""

SYSTEM_PRINCIPAL_ID = "sys:legivellum"
SERVICE_PRINCIPAL_ID = "svc:asyncgate"
INTERNAL_PRINCIPAL_PREFIXES = ("sys:", "svc:")


def is_system(principal_id: str) -> bool:
    """Return True if principal_id matches the canonical system owner."""
    return principal_id == SYSTEM_PRINCIPAL_ID


def normalize_external(principal_id: str) -> str:
    """Normalize external principal IDs without enforcing a prefix scheme."""
    if principal_id.startswith("ext:"):
        return principal_id[4:]
    return principal_id


def normalize_principal_id(principal_id: str) -> str:
    """Normalize principal identifiers for storage and lookup."""
    return normalize_external(principal_id)


def is_internal_principal_id(principal_id: str) -> bool:
    """Return True if principal_id is an internal system/service identifier."""
    return principal_id.startswith(INTERNAL_PRINCIPAL_PREFIXES)


def principal_id_variants(principal_id: str) -> list[str]:
    """
    Return principal ID variants for backward compatibility.

    Includes the normalized ID and the legacy ext: form for external principals.
    """
    normalized = normalize_principal_id(principal_id)
    if is_internal_principal_id(normalized):
        return [normalized]
    return list({normalized, f"ext:{normalized}"})
