import { expect, test } from "@playwright/test";

const token = "console-browser-test-token-not-for-production";
test("schedule CRUD, preview, manual execution and read-only investigation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/schedules");
  await page.getByLabel("Operator token").fill(token);
  await page.getByRole("button", { name: "Open Console" }).click();
  await expect(
    page.getByRole("heading", { name: "Accepted schedules" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create schedule" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/^Schedule ID/).fill("browser-daily");
  await dialog
    .getByLabel("Name", { exact: true })
    .fill("Daily workspace digest");
  await dialog
    .getByLabel("Prompt", { exact: true })
    .fill("Summarize the workspace for browser acceptance.");
  await dialog.getByLabel(/^Project/).fill("project-claw-main");
  await dialog.getByRole("button", { name: "Preview timing" }).click();
  await expect(dialog.getByText(/Preview in UTC/)).toBeVisible();
  await expect(dialog.locator("ol li")).toHaveCount(5);
  await page.screenshot({ path: "../build/console-schedule-editor.png" });
  await dialog.getByRole("button", { name: "Save schedule" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByText("Daily workspace digest", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run now", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirm run now" }).click();
  await dialog.getByRole("button", { name: "Inspect occurrence" }).click();
  await expect(dialog.getByText("completed", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await dialog.getByText("Retained result", { exact: true }).click();
  await expect(
    dialog.getByText("Recorded browser test reply", { exact: true }),
  ).toBeVisible();
  const threadLink = dialog.locator('a[href^="/runs/"]');
  await threadLink.click();
  await expect(
    page.getByRole("heading", { name: "Latest saved attempt" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "History", exact: true }).click();
  await expect(
    page.getByText("Recorded browser test reply", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../build/console-run-history.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Usage", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Token counters" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Configuration", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Captured execution" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Sources", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "browser-daily", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Tools", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Retained tool executions" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Schedules", exact: true }).click();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Run now", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await dialog.getByLabel("Name", { exact: true }).fill("Paused daily digest");
  await dialog.getByRole("button", { name: "Save schedule" }).click();
  await expect(
    page.getByText("Paused daily digest", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "No schedules yet" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "browser-daily", exact: true }),
  ).toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page.getByLabel("Color theme").selectOption(theme);
    for (const width of [1440, 768, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const label of [
        "Overview",
        "Runs",
        "Models & Accounts",
        "Schedules",
      ]) {
        await page
          .getByRole("navigation", { name: "Primary navigation" })
          .getByRole("link", { name: label, exact: true })
          .click();
        await expect(page.locator("h1")).toHaveText(label);
        await expect
          .poll(() =>
            page.evaluate(
              () =>
                document.documentElement.scrollWidth <=
                document.documentElement.clientWidth,
            ),
          )
          .toBe(true);
        await expect(page.getByText("Loading…", { exact: true })).toHaveCount(
          0,
        );
        await page.screenshot({
          path: `../build/console-${label.split(" ")[0].toLowerCase()}-${theme}-${width}.png`,
          fullPage: true,
        });
      }
    }
  }
  expect(errors).toEqual([]);
});
