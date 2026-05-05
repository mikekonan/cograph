import { expect, test } from "@playwright/test";

test("search groups retrieval results by layer", async ({ page }) => {
  await page.goto("/search?repo_id=00000000-0000-0000-0000-000000000001&q=e_repo_not_ready");

  await expect(page.getByRole("heading", { name: "Search" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Code" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "AST Summary" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "AST", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Repo Docs" })).toBeVisible();
  await expect(
    page.getByText("E_REPO_NOT_READY is raised while a repository is still indexing.").first(),
  ).toBeVisible();
});

test("wiki node sources navigate into the graph view", async ({ page }) => {
  await page.goto("/repos/github.com/fastapi/fastapi/wiki/overview");

  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page
    .getByRole("region", { name: "Sources" })
    .getByRole("link", { name: /Graph node.*fastapi\.routing\.APIRouter/ })
    .click();

  await expect(page).toHaveURL(
    /\/repos\/github\.com\/fastapi\/fastapi\/graph\?node=fa-cls-apirouter/,
  );
  await expect(page.getByText("APIRouter", { exact: true }).first()).toBeVisible();
});

test("wiki repo-doc sources open the git-host source URL", async ({ page }) => {
  await page.addInitScript(() => {
    const openedUrls: string[] = [];
    Reflect.set(window, "__openedUrls", openedUrls);
    window.open = ((url?: string | URL) => {
      openedUrls.push(String(url));
      return null;
    }) as typeof window.open;
  });

  await page.goto("/repos/github.com/fastapi/fastapi/wiki/fastapi-routing");

  await expect(page.getByRole("heading", { name: "Fastapi Routing" })).toBeVisible();
  await page
    .getByRole("region", { name: "Sources" })
    .getByRole("button", { name: /Repo doc.*Routing docs/ })
    .click();

  await expect
    .poll(() =>
      page.evaluate(() => {
        const openedUrls = Reflect.get(window, "__openedUrls");
        return Array.isArray(openedUrls) ? (openedUrls[0] ?? null) : null;
      }),
    )
    .toBe("https://github.com/fastapi/fastapi/blob/master/docs/routing.md");
});
