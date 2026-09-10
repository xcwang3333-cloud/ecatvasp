import { describe, expect, it } from "vitest";

import { DESKTOP_V2_OPERATIONS } from "./contracts-v2";
import {
  assertSitePreflightPayload,
  type SitePreflightPayload,
} from "./site-preflight";

describe("site preflight desktop contract", () => {
  it("advertises the explicit site_preflight operation", () => {
    expect(DESKTOP_V2_OPERATIONS).toContain("site_preflight");
  });

  it("accepts sanitized transient preflight evidence", () => {
    const payload: SitePreflightPayload = {
      site_id: "site-a",
      profile_hash: "a".repeat(64),
      status: "READY",
      timestamp: "2026-09-11T00:00:00+00:00",
      transient: true,
      checks: [
        {
          check_name: "vasp_executable",
          status: "READY",
          reason_code: null,
          message: "configured executable resolved",
          evidence: ["/opt/vasp/vasp_std"],
        },
      ],
    };
    expect(() => assertSitePreflightPayload(payload, "site-a")).not.toThrow();
  });

  it("rejects mismatched site identity and malformed profile hashes", () => {
    const payload: SitePreflightPayload = {
      site_id: "site-a",
      profile_hash: "not-a-digest",
      status: "READY",
      timestamp: "2026-09-11T00:00:00+00:00",
      transient: true,
      checks: [],
    };
    expect(() => assertSitePreflightPayload(payload, "site-b")).toThrow(
      "site preflight identity is invalid",
    );
    expect(() => assertSitePreflightPayload(payload, "site-a")).toThrow(
      "site preflight profile hash is invalid",
    );
  });
});
