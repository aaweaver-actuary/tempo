import { test as base, expect } from "@playwright/test";
export { expect };
export const test = base.extend<{ failureEvidence: void }>({
  failureEvidence: [
    async ({ page }, use, testInfo) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });
      await use();
      if (testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach("console-errors", {
          body: JSON.stringify(errors, null, 2),
          contentType: "application/json",
        });
        const geometry = await page
          .locator(
            ".topbar,.persistent-board-shell,.cg-wrap,.shared-board-toolbar,.unified-board-shell-panel,[role=dialog]",
          )
          .evaluateAll((elements) =>
            elements.map((element) => ({
              className: element.className,
              bounds: element.getBoundingClientRect().toJSON(),
            })),
          )
          .catch(() => []);
        await testInfo.attach("geometry", {
          body: JSON.stringify(geometry, null, 2),
          contentType: "application/json",
        });
      }
    },
    { auto: true },
  ],
});
