import { createRootRoute, Outlet } from "@tanstack/react-router";

import { Toaster } from "@/components/Toaster";
import { SessionGate } from "@/session";

export const Route = createRootRoute({
  component: () => (
    <>
      <SessionGate>
        <Outlet />
      </SessionGate>
      <Toaster />
    </>
  ),
});
