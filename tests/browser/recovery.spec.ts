import { test, expect, navigate, prepareUI } from "./ui-fixtures";
test("failed initial loads never display empty records or zero statistics", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Local database unavailable. Check the data mount." },
    }),
  );
  await prepareUI(page);
  for (const workspace of ["Games", "Progress"]) {
    await navigate(page, workspace);
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(page.locator(".games-metrics,.metric-grid")).toHaveCount(0);
    await expect(
      page.getByText("No games imported", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Retry", exact: true }),
    ).toBeVisible();
  }
});
test("failed settings reads cannot overwrite authoritative settings with defaults", async ({
  page,
}) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 503, json: { detail: "Database unavailable" } }),
  );
  await prepareUI(page);
  await navigate(page, "Settings");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save settings" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Retry", exact: true }),
  ).toBeVisible();
});

test("malformed progress is unavailable and retry recovers real measurements", async ({
  page,
}) => {
  let broken = true;
  await page.route("**/api/progress", (route) =>
    route.fulfill({
      json: broken
        ? { unexpected: true }
        : {
            states: { new: 2 },
            activity: [],
            reviewedToday: 3,
            cleanCards: 2,
            dueToday: 1,
            totalCards: 9,
          },
    }),
  );
  await prepareUI(page);
  await navigate(page, "Progress");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(page.locator(".metric-grid")).toHaveCount(0);
  broken = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".metric-grid")).toContainText("9");
});
