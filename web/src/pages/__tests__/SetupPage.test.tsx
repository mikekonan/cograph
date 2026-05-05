import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import SetupPage from "../SetupPage";

// useAuth is only needed for setUser on success — stub it so we don't
// need a full AuthProvider + /api/auth/config + /api/auth/me chain.
vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ setUser: vi.fn() }),
}));

const server = setupServer(
  http.post("/api/auth/bootstrap", () =>
    HttpResponse.json(
      { error: { code: "INTERNAL_ERROR", message: "not configured", request_id: "r" } },
      { status: 500 },
    ),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderSetup() {
  render(
    <MemoryRouter>
      <SetupPage />
    </MemoryRouter>,
  );
}

async function fillAndSubmit(token = "tok", password = "password1234") {
  fireEvent.change(screen.getByLabelText(/setup token/i), { target: { value: token } });
  fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: /create admin account/i }));
}

describe("SetupPage — D9 error parsing via instanceof ApiError", () => {
  it("prefills a backend-valid default email", () => {
    renderSetup();

    expect(screen.getByLabelText(/^email/i)).toHaveValue("admin@example.com");
  });

  it("shows BOOTSTRAP_TOKEN_INVALID message", async () => {
    server.use(
      http.post("/api/auth/bootstrap", () =>
        HttpResponse.json(
          {
            error: {
              code: "BOOTSTRAP_TOKEN_INVALID",
              message: "Invalid token",
              request_id: "r",
            },
          },
          { status: 400 },
        ),
      ),
    );

    renderSetup();
    await fillAndSubmit();

    await waitFor(() => expect(screen.getByText(/invalid setup token/i)).toBeInTheDocument());
  });

  it("shows ADMIN_ALREADY_EXISTS message", async () => {
    server.use(
      http.post("/api/auth/bootstrap", () =>
        HttpResponse.json(
          {
            error: {
              code: "ADMIN_ALREADY_EXISTS",
              message: "Admin already exists",
              request_id: "r",
            },
          },
          { status: 409 },
        ),
      ),
    );

    renderSetup();
    await fillAndSubmit();

    await waitFor(() => expect(screen.getByText(/an admin already exists/i)).toBeInTheDocument());
  });

  it("does NOT fall through to generic message for known ApiError codes", async () => {
    server.use(
      http.post("/api/auth/bootstrap", () =>
        HttpResponse.json(
          {
            error: {
              code: "BOOTSTRAP_TOKEN_INVALID",
              message: "Invalid token",
              request_id: "r",
            },
          },
          { status: 400 },
        ),
      ),
    );

    renderSetup();
    await fillAndSubmit();

    await waitFor(() => screen.getByRole("alert"));
    expect(screen.getByRole("alert").textContent).not.toContain("Setup failed");
  });

  it("renders field-level validation errors from the API", async () => {
    server.use(
      http.post("/api/auth/bootstrap", () =>
        HttpResponse.json(
          {
            error: {
              code: "VALIDATION_FAILED",
              message: "Request validation failed",
              request_id: "r",
              field_errors: [
                {
                  field: "email",
                  code: "INVALID",
                  message: "value is not a valid email address",
                },
              ],
            },
          },
          { status: 422 },
        ),
      ),
    );

    renderSetup();
    await fillAndSubmit();

    await waitFor(() =>
      expect(screen.getByText(/value is not a valid email address/i)).toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/^email/i)).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByText("Request validation failed")).not.toBeInTheDocument();
  });
});
