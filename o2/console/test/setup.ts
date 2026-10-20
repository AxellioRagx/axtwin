/**
 * One setup file, two jobs.
 *
 * **1. React Testing Library's auto-cleanup**, which it only installs itself
 * when a test runner exposes globals. This package runs vitest with
 * `globals: false` (every test imports what it uses), so cleanup is wired here
 * explicitly rather than left to a runner detail.
 *
 * **2. A clean `localStorage` per test** (S5.1.d). jsdom's storage is per
 * WORKER, not per test, and the draft (`src/edit/draft.ts`) deliberately
 * persists an imprint's pending edits across a reload -- so without this, one
 * test's staged records are restored into the next test that opens the same
 * imprint, and the change list starts with edits nobody made. That is the
 * feature working correctly and the harness lying; the harness is what has to
 * change. Cleared BEFORE each test rather than after, so a test that seeds a
 * draft deliberately still has it, and a crashed test cannot poison the next.
 */
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    // No storage in this environment: nothing to reset.
  }
});

afterEach(() => {
  cleanup();
});
