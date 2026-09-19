import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Without vitest globals RTL cannot self-register its cleanup, so every renderHook()/render()
// stayed mounted for the rest of the file -- a hook's retry timer then fired into a later test.
afterEach(cleanup);

// jsdom has no ResizeObserver; LightweightChart observes its container with one.
globalThis.ResizeObserver ??= class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
};
