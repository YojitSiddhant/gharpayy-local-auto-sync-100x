import { describe, expect, it } from "vitest";
import { z } from "zod";

describe("foundation API contract", () => {
  it("accepts the backend health shape", () => {
    const schema = z.object({ status: z.string(), service: z.string() });
    expect(schema.parse({ status: "ok", service: "gharpayy-backend" }).status).toBe("ok");
  });
});
