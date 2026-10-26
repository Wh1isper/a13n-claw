import { lazy, Suspense, useEffect, useState } from "react";
import {
  createBrowserRouter,
  NavLink,
  Outlet,
  RouterProvider,
  useLocation,
} from "react-router";
import { Session, useLogout } from "./api/session";
import { Empty, Loading } from "./components/ui";

const Overview = lazy(() =>
  import("./pages/overview").then((module) => ({ default: module.Overview })),
);
const Models = lazy(() =>
  import("./pages/models").then((module) => ({ default: module.Models })),
);
const Runs = lazy(() =>
  import("./pages/runs").then((module) => ({ default: module.Runs })),
);
const Schedules = lazy(() =>
  import("./pages/schedules").then((module) => ({ default: module.Schedules })),
);
const RunDetail = lazy(() =>
  import("./pages/runs").then((module) => ({ default: module.RunDetail })),
);
import { LoginMonitor } from "./components/account-login";

const navigation = [
  {
    to: "/",
    label: "Overview",
    path: "M3 13h7V3H3zm11 8h7V11h-7zM3 21h7v-4H3zM14 7h7V3h-7z",
  },
  { to: "/runs", label: "Runs", path: "m4 6 5 5-5 5m8 2h8" },
  {
    to: "/models",
    label: "Models & Accounts",
    path: "M12 3 3 8l9 5 9-5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5",
  },
  {
    to: "/schedules",
    label: "Schedules",
    path: "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  },
];
function Shell() {
  const logout = useLogout();
  const [theme, setTheme] = useState("system");
  const location = useLocation();
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    document.title = `${navigation.find((item) => (item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to)))?.label ?? "Console"} · A13n Claw`;
  }, [location.pathname]);
  return (
    <div className="shell">
      <LoginMonitor />
      <a className="skip" href="#content">
        Skip to content
      </a>
      <aside className="sidebar">
        <NavLink className="brand" to="/">
          <span className="brand-mark" aria-hidden="true">
            a
          </span>
          Claw
        </NavLink>
        <nav aria-label="Primary navigation">
          {navigation.map((item) => (
            <NavLink to={item.to} end={item.to === "/"} key={item.to}>
              <svg
                aria-hidden="true"
                width="17"
                height="17"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d={item.path} />
              </svg>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <p className="muted">Your assistant. Your infrastructure.</p>
          <select
            aria-label="Color theme"
            value={theme}
            onChange={(event) => setTheme(event.target.value)}
          >
            <option value="system">System</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
          <button onClick={logout}>Disconnect</button>
        </div>
      </aside>
      <main id="content" className="content">
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
const router = createBrowserRouter([
  {
    element: (
      <Session>
        <Shell />
      </Session>
    ),
    children: [
      { index: true, element: <Overview /> },
      { path: "models", element: <Models /> },
      { path: "schedules", element: <Schedules /> },
      { path: "runs", element: <Runs /> },
      { path: "runs/:threadId", element: <RunDetail /> },
      {
        path: "*",
        element: (
          <Empty title="Page not found">
            Use the navigation to return to your Console.
          </Empty>
        ),
      },
    ],
  },
]);
export function App() {
  return <RouterProvider router={router} />;
}
