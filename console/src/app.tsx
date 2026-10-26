import { lazy, Suspense, useEffect, useState } from "react";
import {
  createBrowserRouter,
  NavLink,
  Outlet,
  RouterProvider,
  useLocation,
} from "react-router";
import { Session, useLogout } from "./api/session";
import { Icon, IconButton, type IconName } from "./components/icons";
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
    icon: "overview",
  },
  { to: "/runs", label: "Runs", icon: "runs" },
  {
    to: "/models",
    label: "Models & Accounts",
    icon: "models",
  },
  {
    to: "/schedules",
    label: "Schedules",
    icon: "calendar",
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
            <NavLink
              to={item.to}
              end={item.to === "/"}
              key={item.to}
              title={item.label}
              aria-label={item.label}
            >
              <Icon name={item.icon as IconName} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="theme-picker">
            <Icon
              name={
                theme === "dark"
                  ? "moon"
                  : theme === "light"
                    ? "sun"
                    : "monitor"
              }
            />
            <select
              aria-label="Color theme"
              title="Color theme"
              value={theme}
              onChange={(event) => setTheme(event.target.value)}
            >
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
          <IconButton icon="logout" label="Disconnect" onClick={logout} />
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
