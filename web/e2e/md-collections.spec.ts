import { expect, test } from "@playwright/test";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@cograph.local");
  await page.getByLabel("Password").fill("admin123");
  await page.getByRole("main").getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL("/");
}

test("md collections page loads and shows upload UI", async ({ page }) => {
  await login(page);
  // Use client-side navigation so MSW auth state survives
  await page.getByRole("link", { name: "Docs" }).click();
  await expect(page).toHaveURL("/docs");

  await expect(page.getByRole("heading", { name: "Collections" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add" })).toBeVisible();
});

test("md collection detail shows drag-drop zone and jobs panel", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Docs" }).click();
  await expect(page).toHaveURL("/docs");

  await page.getByRole("button", { name: "Add" }).click();

  // Wait for the dialog form to be fully hydrated
  await page.waitForSelector('input[placeholder="Name"]');
  await page.getByPlaceholder("Name").fill("E2E Test Collection");
  await page.getByPlaceholder("Description (optional)").fill("A test collection");
  await page.getByRole("button", { name: "Create" }).click();

  // Collection should appear in the list
  await expect(page.getByText("E2E Test Collection")).toBeVisible();

  // Click to open detail page
  await page.getByRole("link", { name: "Open E2E Test Collection" }).click();
  await expect(page).toHaveURL(/\/docs\/.+/);

  // Upload area should be visible
  await expect(page.getByText("Batch Upload")).toBeVisible();
  await expect(page.getByText("Drag and drop markdown files here")).toBeVisible();
});
