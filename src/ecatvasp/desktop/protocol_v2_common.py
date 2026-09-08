"""Shared desktop IPC v2 contract identifiers and explicit operation catalog."""

from __future__ import annotations

from enum import StrEnum

DESKTOP_IPC_V2_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v2"


class DesktopV2Operation(StrEnum):
    """Explicit task operations available through the v1.1 desktop IPC v2 contract."""

    HEALTH = "health"
    OPEN_PROJECT = "open_project"
    STATUS = "status"
    FRONTEND_HANDOFF = "frontend_handoff"
    APPLICATION_REPORT = "application_report"
    PREPARE_WORKFLOW = "prepare_workflow"
    PROJECT_DASHBOARD = "project_dashboard"
    MODEL_CATALOG = "model_catalog"
    STRUCTURE_PRESENTATION = "structure_presentation"
    CREATE_PROJECT = "create_project"
    CREATE_CATALYST = "create_catalyst"
    BUILD_GRAPHENE_MODEL = "build_graphene_model"
    IMPORT_STRUCTURE_MODEL = "import_structure_model"
    MUTATE_STRUCTURE_MODEL = "mutate_structure_model"
    BUILD_SINGLE_METAL_SITE = "build_single_metal_site"
    BUILD_MULTI_METAL_SITE = "build_multi_metal_site"
    CREATE_ACTIVE_SITE = "create_active_site"
    BUILD_ADSORBATE_CONFORMER = "build_adsorbate_conformer"
    CALCULATION_CATALOG = "calculation_catalog"
    PREPARE_CALCULATION_WORKFLOW = "prepare_calculation_workflow"
    MATERIALIZE_CALCULATION_STEP = "materialize_calculation_step"
