import { queryClient } from "@/api/queryClient";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { router } from "@/router/router";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { RouterProvider } from "react-router";

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <TooltipProvider delayDuration={250} skipDelayDuration={100}>
            <RouterProvider router={router} />
          </TooltipProvider>
          <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
