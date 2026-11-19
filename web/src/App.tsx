import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useSession } from "./lib/session";
import { Loading } from "./ui";

// The landing page loads alone; everything behind sign-in, and the generator
// (which carries the whole reference source), arrive as separate chunks.
const Landing = lazy(() => import("./pages/public/Landing"));
const Generator = lazy(() => import("./pages/public/Generator"));
const NotFound = lazy(() => import("./pages/public/NotFound"));
const Login = lazy(() => import("./pages/auth/Login"));
const Signup = lazy(() => import("./pages/auth/Signup"));
const Forgot = lazy(() => import("./pages/auth/Forgot"));
const Reset = lazy(() => import("./pages/auth/Reset"));
const Verify = lazy(() => import("./pages/auth/Verify"));
const Invite = lazy(() => import("./pages/auth/Invite"));
const Shell = lazy(() => import("./layout/Shell"));
const AppIndex = lazy(() => import("./pages/app/AppIndex"));
const Overview = lazy(() => import("./pages/app/Overview"));
const Gateways = lazy(() => import("./pages/app/Gateways"));
const Payments = lazy(() => import("./pages/app/Payments"));
const PaymentDetail = lazy(() => import("./pages/app/PaymentDetail"));
const Links = lazy(() => import("./pages/app/Links"));
const Developers = lazy(() => import("./pages/app/Developers"));
const Team = lazy(() => import("./pages/app/Team"));
const Settings = lazy(() => import("./pages/app/Settings"));
const Audit = lazy(() => import("./pages/app/Audit"));
const Account = lazy(() => import("./pages/app/Account"));
const Admin = lazy(() => import("./pages/app/Admin"));

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useSession();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/generator" element={<Generator />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/forgot-password" element={<Forgot />} />
        <Route path="/reset-password" element={<Reset />} />
        <Route path="/verify-email" element={<Verify />} />
        <Route path="/invite" element={<Invite />} />
        <Route path="/app" element={<RequireAuth><AppIndex /></RequireAuth>} />
        <Route path="/account" element={<RequireAuth><Account /></RequireAuth>} />
        <Route path="/admin" element={<RequireAuth><Admin /></RequireAuth>} />
        <Route path="/app/:slug/:mode" element={<RequireAuth><Shell /></RequireAuth>}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<Overview />} />
          <Route path="gateways" element={<Gateways />} />
          <Route path="payments" element={<Payments />} />
          <Route path="payments/:orderId" element={<PaymentDetail />} />
          <Route path="links" element={<Links />} />
          <Route path="developers" element={<Developers />} />
          <Route path="developers/:tab" element={<Developers />} />
          <Route path="team" element={<Team />} />
          <Route path="settings" element={<Settings />} />
          <Route path="audit" element={<Audit />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
