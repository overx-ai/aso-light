import { useNavigate, useLocation } from "react-router-dom";
import { NavLink, Image } from "@mantine/core";
import {
  IconDeviceMobile,
  IconCoin,
  IconKeyboard,
  IconWorld,
  IconFileDescription,
  IconLanguage,
  IconCash,
  IconMessage,
  IconChartBar,
  IconChecks,
  IconSwords,
} from "@tabler/icons-react";
import type { App } from "@/types";

interface AppNavItemProps {
  app: App;
  onNavigate?: () => void;
}

const SUB_ROUTES = [
  { path: "pricing", label: "Pricing", icon: IconCoin },
  { path: "keywords", label: "Keywords", icon: IconKeyboard },
  { path: "availability", label: "Availability", icon: IconWorld },
  { path: "metadata", label: "Metadata", icon: IconFileDescription },
  { path: "aso-check", label: "ASO Check", icon: IconChecks },
  { path: "cross-localization", label: "Cross-Loc", icon: IconLanguage },
  { path: "reviews", label: "Reviews", icon: IconMessage },
  { path: "visibility", label: "Visibility", icon: IconChartBar },
  { path: "clash", label: "App Clash", icon: IconSwords },
  { path: "revenuecat", label: "RevenueCat", icon: IconCash },
];

export default function AppNavItem({ app, onNavigate }: AppNavItemProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const appBasePath = `/apps/${app.id}`;
  const isActive = location.pathname.startsWith(appBasePath);

  const icon = app.icon_url ? (
    <Image src={app.icon_url} alt={app.name} w={20} h={20} radius={4} />
  ) : (
    <IconDeviceMobile size={20} />
  );

  return (
    <NavLink
      label={app.name}
      leftSection={icon}
      active={isActive}
      defaultOpened={isActive}
      childrenOffset={32}
    >
      {SUB_ROUTES.map((sub) => {
        const path = `${appBasePath}/${sub.path}`;
        return (
          <NavLink
            key={sub.path}
            label={sub.label}
            leftSection={<sub.icon size={16} />}
            active={location.pathname === path}
            onClick={() => {
              navigate(path);
              onNavigate?.();
            }}
          />
        );
      })}
    </NavLink>
  );
}
