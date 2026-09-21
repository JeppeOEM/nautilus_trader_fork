import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router";

import ErrorBar from "./components/ErrorBar";
import { useAlertToasts } from "./hooks/useAlertToasts";

// Route-based code-splitting (spine Consistency Conventions): each page is its own lazy chunk,
// so the rankings/history/docs bundles never block the chart page's and vice versa.
const RankingsPage = lazy(() => import("./pages/RankingsPage"));
const ChartPage = lazy(() => import("./pages/ChartPage"));
const HistoryPage = lazy(() => import("./pages/HistoryPage"));
const DocsPage = lazy(() => import("./pages/DocsPage"));
const AlertsPage = lazy(() => import("./pages/AlertsPage"));

const queryClient = new QueryClient();

function TopNav() {
  return (
    <nav style={{ borderBottom: "1px solid var(--color-border)", padding: "0.6em 1em" }}>
      <Link to="/">Rankings</Link> · <Link to="/alerts">Alerts</Link> · <Link to="/docs">Docs</Link>
    </nav>
  );
}

function AlertToasts() {
  const toasts = useAlertToasts();
  return (
    <div role="status" style={{ position: "fixed", right: "1em", bottom: "1em", zIndex: 1000 }}>
      {toasts.map((t) => (
        <p key={t.id} style={{ background: "var(--color-surface, #222)", padding: "0.5em 1em", margin: "0.3em 0" }}>
          {t.message}
        </p>
      ))}
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <TopNav />
        <ErrorBar />
        <AlertToasts />
        <Suspense fallback={<p className="term-loading">Loading</p>}>
          <Routes>
            <Route path="/" element={<RankingsPage />} />
            <Route path="/chart/:iid" element={<ChartPage />} />
            <Route path="/history/:iid" element={<HistoryPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/docs/*" element={<DocsPage />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
