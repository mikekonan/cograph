import { http, HttpResponse } from "msw";

export const healthHandlers = [
  http.get("/api/health", () =>
    HttpResponse.json({
      status: "healthy",
      database: "connected",
      version: "0.1.0-mock",
    }),
  ),
];
