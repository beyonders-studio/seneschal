import { configure } from "@testing-library/react";

// findBy and waitFor keep their own ceiling, apart from vitest's testTimeout,
// and it defaults to one second. A whole-suite run has every worker loading
// pages at once, so a query that resolves promptly alone can wait past that
// and fail on the clock rather than on what it checks. Kept well under
// testTimeout, so a wait that is really stuck still reports as that wait.
configure({ asyncUtilTimeout: 5_000 });
