import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Nav from "./components/Nav";
import GeographicRisk from "./pages/GeographicRisk";
import { AuthProvider, ProtectedRoute, useAuth } from "./lib/auth";
import Home from "./pages/Home";
import Login from "./pages/Login";

const FingerprintHunt = lazy(() => import("./pages/FingerprintHunt"));
const Dashboard    = lazy(() => import("./pages/Dashboard"));
const Search       = lazy(() => import("./pages/Search"));
const EntityDetail = lazy(() => import("./pages/EntityDetail"));
const NetworkExplorer = lazy(() => import("./pages/NetworkExplorer"));
const Cases        = lazy(() => import("./pages/Cases"));
const CaseWorkspace = lazy(() => import("./pages/CaseWorkspace"));
const ReportBuilder = lazy(() => import("./pages/ReportBuilder"));
const Patterns     = lazy(() => import("./pages/Patterns"));
const PatternDetail = lazy(() => import("./pages/PatternDetail"));
const Alerts       = lazy(() => import("./pages/Alerts"));
const Admin        = lazy(() => import("./pages/Admin"));
const Hunt         = lazy(() => import("./pages/Hunt"));
const Ingestion    = lazy(() => import("./pages/Ingestion"));
const Security     = lazy(() => import("./pages/Security"));

function RedirectToLogin() {
  const { pathname, search } = useLocation();
  return <Navigate to={`/login?next=${encodeURIComponent(pathname + search)}`} replace />;
}

function AppShell() {
  const { user } = useAuth();
  if (!user) {
    return (
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<RedirectToLogin />} />
      </Routes>
    );
  }
  return (
    <div className="app-shell">
      <Nav />
      <div className="main-area">
        <Suspense fallback={<main><p className="hint" style={{ marginTop: 60, textAlign: "center" }}>Loading…</p></main>}>
          <Routes>
            <Route path="/login" element={<Navigate to="/" replace />} />
            <Route path="/" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
            <Route path="/search" element={<ProtectedRoute><Search /></ProtectedRoute>} />
            <Route path="/entity/:type/:value" element={<ProtectedRoute><EntityDetail /></ProtectedRoute>} />
            <Route path="/network" element={<ProtectedRoute><NetworkExplorer /></ProtectedRoute>} />
            <Route path="/cases" element={<ProtectedRoute><Cases /></ProtectedRoute>} />
            <Route path="/cases/:id" element={<ProtectedRoute><CaseWorkspace /></ProtectedRoute>} />
            <Route path="/cases/:id/report" element={<ProtectedRoute><ReportBuilder /></ProtectedRoute>} />
            <Route path="/patterns" element={<ProtectedRoute><Patterns /></ProtectedRoute>} />
            <Route path="/patterns/:id" element={<ProtectedRoute><PatternDetail /></ProtectedRoute>} />
            <Route path="/geo-risk" element={<ProtectedRoute><GeographicRisk /></ProtectedRoute>} />
            <Route path="/alerts" element={<ProtectedRoute><Alerts /></ProtectedRoute>} />
            <Route path="/biometric/fingerprint" element={<ProtectedRoute><FingerprintHunt /></ProtectedRoute>} />
            <Route path="/hunt" element={<ProtectedRoute><Hunt /></ProtectedRoute>} />
            <Route path="/ingestion" element={<ProtectedRoute><Ingestion /></ProtectedRoute>} />
            <Route path="/security" element={<ProtectedRoute><Security /></ProtectedRoute>} />
            <Route path="/admin" element={<ProtectedRoute roles={["admin"]}><Admin /></ProtectedRoute>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}
