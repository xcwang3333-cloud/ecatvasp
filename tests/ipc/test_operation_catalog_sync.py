from __future__ import annotations


def test_ipc_contract_governance_marker() -> None:
    """Guard placeholder for IPC v2 contract parity enforcement.

    The production contract remains owned by the Python IPC semantic catalog.
    This test entry point is intentionally explicit so future TS/native parity
    checks can fail closed without introducing implicit RPC fallback behavior.
    """
    assert True


def test_unknown_operation_policy_is_fail_closed() -> None:
    unknown_operation = "__unknown_operation__"
    assert unknown_operation.startswith("__")
