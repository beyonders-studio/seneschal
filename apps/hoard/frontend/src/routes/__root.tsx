import { createRootRoute, Outlet } from "@tanstack/react-router";

import { Toaster } from "@/components/Toaster";
import { CommunityWords } from "@/features/settings/WordsSection";
import { SessionGate } from "@/session";

export const Route = createRootRoute({
  component: () => (
    <>
      <SessionGate>
        <CommunityWords />
        <Outlet />
      </SessionGate>
      <Toaster />
    </>
  ),
});
