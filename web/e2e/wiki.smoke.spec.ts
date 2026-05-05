import { expect, test } from "@playwright/test";

test("wiki page renders markdown content and headings", async ({ page }) => {
  await page.goto("/repos/github.com/fastapi/fastapi/wiki/overview");

  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText(/FastAPI's generated wiki pulls together/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Request path" })).toBeVisible();
});

test("wiki sidebar renders tree and navigates between pages", async ({ page }) => {
  await page.goto("/repos/github.com/fastapi/fastapi/wiki/overview");

  const sidebar = page.getByLabel(/documentation navigation/i);
  await expect(sidebar.getByRole("link", { name: "Overview" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "Fastapi Routing" })).toBeVisible();

  await sidebar.getByRole("link", { name: "Fastapi Routing" }).click();

  await expect(page).toHaveURL(/\/wiki\/fastapi-routing$/);
  await expect(page.getByRole("heading", { name: "Fastapi Routing" })).toBeVisible();
});
