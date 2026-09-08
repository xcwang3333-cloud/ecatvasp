import { describe, expect, it } from "vitest";

import {
  CALCULATION_IPC_VERSION,
  assertCalculationCatalogPayload,
  parseCalculationResponse,
  type CalculationCatalogPayload,
} from "./contracts";

describe("calculation wizard desktop contract", () => {
  it("parses a correlated typed calculation response", () => {
    const payload = parseCalculationResponse<CalculationCatalogPayload>(
      JSON.stringify({
        protocol_version: CALCULATION_IPC_VERSION,
        request_id: "calc-1",
        operation: "calculation_catalog",
        ok: true,
        payload: {
          project_root: "C:/project",
          catalog: { roots: [], tasks: [] },
        },
      }),
      "calculation_catalog",
      "calc-1",
    );
    expect(() => assertCalculationCatalogPayload(payload, "C:/project")).not.toThrow();
  });

  it("rejects response correlation drift", () => {
    const raw = JSON.stringify({
      protocol_version: CALCULATION_IPC_VERSION,
      request_id: "other",
      operation: "calculation_catalog",
      ok: true,
      payload: {},
    });
    expect(() => parseCalculationResponse(raw, "calculation_catalog", "calc-1")).toThrow(
      "correlation mismatch",
    );
  });

  it("surfaces backend scientific rejection without reinterpretation", () => {
    const raw = JSON.stringify({
      protocol_version: CALCULATION_IPC_VERSION,
      request_id: "calc-2",
      operation: "materialize_calculation_step",
      ok: false,
      error: {
        code: "application_rejected",
        message: "solid calculation materialization requires validated k-point evidence",
      },
    });
    expect(() =>
      parseCalculationResponse(raw, "materialize_calculation_step", "calc-2"),
    ).toThrow("requires validated k-point evidence");
  });
});
