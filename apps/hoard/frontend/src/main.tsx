import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@/styles.css";
import "@/i18n";
import { connectKit } from "@/lib/kitHost";
import { createQueryClient } from "@/queryClient";
import { createAppRouter } from "@/router";

connectKit();

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={createAppRouter()} />
    </QueryClientProvider>
  </StrictMode>
);
