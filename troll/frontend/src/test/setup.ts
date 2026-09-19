import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver; LightweightChart observes its container with one.
globalThis.ResizeObserver ??= class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
};
