import { expect, test } from "@playwright/test";

const token = "console-browser-test-token-not-for-production";
test("models, credentials, accounts and groups use native configuration with keyboard-safe dialogs", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/models?tab=credentials");
  await page.getByLabel("Operator token").fill(token);
  await page.getByRole("button", { name: "Open Console" }).click();
  await page.getByRole("button", { name: "Add credential" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Credential reference").fill("key-browser");
  await dialog.getByLabel("API key").fill("fixture-not-a-real-provider-key");
  await dialog.getByRole("button", { name: "Store key" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("key-browser", { exact: true })).toBeVisible();
  await expect(
    page.getByText("fixture-not-a-real-provider-key", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Models", exact: true }).click();
  const add = page.getByRole("button", { name: "Add model" });
  await add.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 24; i++) {
    await page.keyboard.press("Tab");
    await expect
      .poll(() =>
        dialog.evaluate((node) => node.contains(document.activeElement)),
      )
      .toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(add).toBeFocused();
  await add.click();
  await dialog.getByLabel("Name", { exact: true }).fill("Browser model");
  await dialog.getByLabel("Resource ID").fill("model-browser");
  await dialog
    .getByRole("combobox", { name: "Connection", exact: true })
    .selectOption("openai-chat");
  await dialog.getByLabel(/^Provider model ID/).fill("gpt-4o");
  await dialog.getByLabel("Credential source").selectOption("reference");
  await dialog.getByLabel(/^Credential reference/).fill("key-browser");
  await page.screenshot({ path: "../build/console-model-editor.png" });
  await page.setViewportSize({ width: 320, height: 740 });
  await expect
    .poll(() => dialog.evaluate((node) => node.scrollWidth <= node.clientWidth))
    .toBe(true);
  await dialog
    .getByRole("button", { name: "Save model" })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: "../build/console-model-editor-mobile.png" });
  await dialog.getByRole("button", { name: "Save model" }).click();
  await expect(dialog).toHaveCount(0);
  let row = page.getByRole("row").filter({ hasText: "model-browser" });
  await expect(row).toContainText("key-browser");
  await row.getByRole("button", { name: "Edit" }).click();
  await dialog
    .getByLabel("Name", { exact: true })
    .fill("Renamed browser model");
  await dialog.getByRole("button", { name: "Save model" }).click();
  await expect(row).toContainText("Renamed browser model");
  await row.getByRole("button", { name: "Delete" }).click();
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("link", { name: "Accounts", exact: true }).click();
  await page.getByRole("button", { name: "Add account" }).click();
  await dialog.getByLabel("Name", { exact: true }).fill("Browser subscription");
  await dialog.getByRole("button", { name: "Save account" }).click();
  await expect(dialog).toHaveCount(0);
  const account = page.locator("section.panel").filter({
    has: page.getByRole("heading", {
      name: "Browser subscription",
      exact: true,
    }),
  });
  await expect(
    account.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeEnabled();
  await expect(
    account.getByRole("button", { name: "Reset vouchers" }),
  ).toBeDisabled();
  await page.screenshot({
    path: "../build/console-accounts.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Groups", exact: true }).click();
  await page.getByRole("button", { name: "Add group" }).click();
  await dialog.getByLabel("Name", { exact: true }).fill("Browser group");
  await dialog.getByLabel("Resource ID").fill("subscription-group-browser");
  await dialog
    .getByLabel("Add account")
    .selectOption({ label: "Browser subscription" });
  await dialog.getByLabel("Strategy").selectOption("weighted");
  await dialog.getByRole("spinbutton").fill("3");
  await dialog.getByRole("button", { name: "Save group" }).click();
  await expect(dialog).toHaveCount(0);
  row = page.getByRole("row").filter({ hasText: "subscription-group-browser" });
  await expect(row).toContainText("weighted");
  await row.getByRole("button", { name: "Delete" }).click();
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("link", { name: "Accounts", exact: true }).click();
  await account.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(account).toHaveCount(0);
  await page.getByRole("button", { name: "Disconnect" }).click();
  await expect(page.getByLabel("Operator token")).toHaveValue("");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Open Console" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("ambiguous account creation blocks repeats; conclusive read failures can be refreshed", async ({
  page,
}) => {
  await page.goto("/models?tab=accounts");
  await page.getByLabel("Operator token").fill(token);
  await page.getByRole("button", { name: "Open Console" }).click();
  let creates = 0;
  await page.route("**/api/subscriptions/accounts", async (route) => {
    creates++;
    await route.fulfill({
      status: 503,
      json: {
        error: {
          code: "fixture_failure",
          message: "Response lost after submission",
        },
      },
    });
  });
  await page.getByRole("button", { name: "Add account" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name", { exact: true }).fill("Uncertain account");
  await dialog.getByRole("button", { name: "Save account" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "Response lost after submission",
  );
  await expect(
    dialog.getByRole("button", { name: "Save account" }),
  ).toBeDisabled();
  await dialog.getByLabel("Name", { exact: true }).press("Enter");
  expect(creates).toBe(1);
  await page.keyboard.press("Escape");
  await page.route("**/api/models", (route) =>
    route.fulfill({
      status: 503,
      json: { error: { message: "Catalog temporarily unavailable" } },
    }),
  );
  await page
    .getByRole("button", { name: "Refresh", exact: true })
    .first()
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Catalog temporarily unavailable",
  );
  await page.unroute("**/api/models");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
