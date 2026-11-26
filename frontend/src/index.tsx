import { bridgeVersion, type AppContribution } from "@a13n/console";
import { ConnectorsPage } from "./pages/connectors";
import { Deliveries } from "./pages/deliveries";
import { Dashboard } from "./pages/overview";
import { SchedulesPage } from "./pages/schedules";
import { ConfigurationPage } from "./pages/settings";
export { compactNumber } from "./pages/overview";

export default {
  bridge: bridgeVersion,
  navigation: { label: { en: "Claw", "zh-CN": "Claw" }, icon: "robot" },
  pages: [
    {
      id: "overview",
      label: { en: "Overview", "zh-CN": "总览" },
      component: Dashboard,
    },
    {
      id: "deliveries",
      label: { en: "Deliveries", "zh-CN": "投递记录" },
      component: Deliveries,
    },
    {
      id: "connectors",
      label: { en: "Connectors", "zh-CN": "连接器" },
      component: ConnectorsPage,
    },
    {
      id: "schedules",
      label: { en: "Schedules", "zh-CN": "排程" },
      component: SchedulesPage,
    },
  ],
  settings: [
    {
      id: "settings",
      label: { en: "Claw settings", "zh-CN": "Claw 设置" },
      component: ConfigurationPage,
      permission: "admin",
      scope: "workspace",
    },
  ],
} satisfies AppContribution;
