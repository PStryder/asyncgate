"""Obligation termination logic for receipt chains.

This module defines how obligations are discharged through receipt chains.
AsyncGate is a ledger, not a message broker - obligations persist until
explicitly terminated by receipt evidence.

Key Concepts:
- Obligation: A receipt that creates a commitment (task.assigned, lease.granted, etc.)
- Terminal Receipt: A receipt that discharges an obligation (task.completed, etc.)
- Parent Linkage: Terminal receipts MUST reference their obligation via parents field
- Open Obligation: An obligation without a terminal child receipt

Termination Rules:
- task.assigned → terminated by [task.completed, task.failed, task.canceled]
- lease.granted → terminated by [lease.released, lease.expired]
- schedule.created → terminated by [schedule.deleted, schedule.failed]

Without parent linkage, obligations remain open forever (haunted bootstrap).
"""

from typing import Optional

from asyncgate.models.enums import ReceiptType
from asyncgate.models.receipt import Receipt


# Termination registry: maps obligation types to their terminal receipt types
TERMINATION_RULES: dict[ReceiptType, list[ReceiptType]] = {
    # Task lifecycle
    ReceiptType.TASK_ASSIGNED: [
        ReceiptType.TASK_COMPLETED,
        ReceiptType.TASK_FAILED,
        ReceiptType.TASK_CANCELED,
    ],
    # Lease lifecycle (if we add LEASE_GRANTED in future)
    # ReceiptType.LEASE_GRANTED: [
    #     ReceiptType.LEASE_RELEASED,
    #     ReceiptType.LEASE_EXPIRED,
    # ],
}


def get_terminal_types(obligation_type: ReceiptType) -> list[ReceiptType]:
    """
    Get receipt types that can terminate an obligation.
    
    Args:
        obligation_type: The receipt type that creates an obligation
        
    Returns:
        List of receipt types that can terminate this obligation
        Empty list if obligation_type is not registered
    """
    return TERMINATION_RULES.get(obligation_type, [])


def is_terminal_type(receipt_type: ReceiptType) -> bool:
    """
    Check if a receipt type is terminal (can discharge obligations).
    
    Args:
        receipt_type: Receipt type to check
        
    Returns:
        True if this receipt type appears in any termination rule
    """
    for terminal_types in TERMINATION_RULES.values():
        if receipt_type in terminal_types:
            return True
    return False


def can_terminate(
    terminal_receipt: Receipt,
    obligation_receipt: Receipt,
) -> bool:
    """
    Check if a terminal receipt can terminate a specific obligation.
    
    Args:
        terminal_receipt: Receipt that might terminate the obligation
        obligation_receipt: The obligation receipt to check
        
    Returns:
        True if terminal_receipt can discharge obligation_receipt
    """
    # Must be in termination rules
    terminal_types = get_terminal_types(obligation_receipt.receipt_type)
    if terminal_receipt.receipt_type not in terminal_types:
        return False
    
    # Must reference obligation as parent (explicit chain linkage)
    if obligation_receipt.receipt_id not in terminal_receipt.parents:
        return False
    
    # Must be same tenant
    if terminal_receipt.tenant_id != obligation_receipt.tenant_id:
        return False
    
    return True


def is_obligation_terminated(
    obligation: Receipt,
    potential_terminators: list[Receipt],
) -> tuple[bool, Optional[Receipt]]:
    """
    Check if an obligation has been terminated by any receipt in a list.
    
    This is the core ledger query: "Does evidence exist that discharges
    this obligation?"
    
    Args:
        obligation: The obligation receipt to check
        potential_terminators: List of receipts that might terminate it
        
    Returns:
        Tuple of (is_terminated: bool, terminating_receipt: Receipt | None)
    """
    for receipt in potential_terminators:
        if can_terminate(receipt, obligation):
            return True, receipt
    
    return False, None


def get_terminating_receipt(
    obligation: Receipt,
    ledger: list[Receipt],
) -> Optional[Receipt]:
    """
    Find the receipt that terminated an obligation from the full ledger.
    
    Args:
        obligation: Obligation to check
        ledger: Complete list of receipts to search
        
    Returns:
        The terminating receipt if found, None otherwise
    """
    terminal_types = get_terminal_types(obligation.receipt_type)
    if not terminal_types:
        return None
    
    # Find receipts that could terminate this obligation
    candidates = [
        r for r in ledger
        if r.receipt_type in terminal_types
        and obligation.receipt_id in r.parents
        and r.tenant_id == obligation.tenant_id
    ]
    
    if not candidates:
        return None
    
    # Return most recent if multiple (shouldn't happen, but defensive)
    return max(candidates, key=lambda r: r.created_at)
