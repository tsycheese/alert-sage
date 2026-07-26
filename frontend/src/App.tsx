import { Spin } from "antd";
import { lazy, Suspense } from "react";
import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router";

import { AppLayout } from "./components/AppLayout";

const AlertListPage = lazy(() =>
  import("./pages/alerts/AlertListPage").then((module) => ({ default: module.AlertListPage })),
);
const AlertCreatePage = lazy(() =>
  import("./pages/alerts/AlertCreatePage").then((module) => ({ default: module.AlertCreatePage })),
);
const AlertDetailPage = lazy(() =>
  import("./pages/alerts/AlertDetailPage").then((module) => ({ default: module.AlertDetailPage })),
);
const KnowledgeSearchPage = lazy(() =>
  import("./pages/knowledge/KnowledgeSearchPage").then((module) => ({
    default: module.KnowledgeSearchPage,
  })),
);
const EvaluationListPage = lazy(() =>
  import("./pages/evaluations/EvaluationListPage").then((module) => ({
    default: module.EvaluationListPage,
  })),
);
const EvaluationDetailPage = lazy(() =>
  import("./pages/evaluations/EvaluationDetailPage").then((module) => ({
    default: module.EvaluationDetailPage,
  })),
);
const EvaluationComparePage = lazy(() =>
  import("./pages/evaluations/EvaluationComparePage").then((module) => ({
    default: module.EvaluationComparePage,
  })),
);
const NotFoundPage = lazy(() =>
  import("./pages/NotFoundPage").then((module) => ({ default: module.NotFoundPage })),
);

function RouteFallback(): ReactElement {
  return (
    <div className="route-fallback" role="status" aria-label="页面加载中">
      <Spin size="large" />
    </div>
  );
}

export function App(): ReactElement {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<Navigate to="/alerts" replace />} />
          <Route path="alerts" element={<AlertListPage />} />
          <Route path="alerts/new" element={<AlertCreatePage />} />
          <Route path="alerts/:alertId" element={<AlertDetailPage />} />
          <Route path="knowledge" element={<KnowledgeSearchPage />} />
          <Route path="evaluations" element={<EvaluationListPage />} />
          <Route path="evaluations/compare" element={<EvaluationComparePage />} />
          <Route path="evaluations/:runId" element={<EvaluationDetailPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
