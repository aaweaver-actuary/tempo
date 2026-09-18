import { expect, it } from "vitest";
import { assertDisposableTarget } from "../browser/disposable-target";
it("destructive browser fixtures refuse production and unmarked services", () => {
  for (const health of [
    null,
    {},
    { status: "ok" },
    { test_instance: false },
    { test_instance: "true" },
  ])
    expect(() => assertDisposableTarget(health)).toThrow(
      "Refusing destructive fixtures",
    );
  expect(() => assertDisposableTarget({ test_instance: true })).not.toThrow();
});
