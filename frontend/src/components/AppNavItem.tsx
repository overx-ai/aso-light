import { useNavigate, useLocation } from "react-router-dom";
import { NavLink, Image } from "@mantine/core";
import { IconDeviceMobile } from "@tabler/icons-react";
import type { App } from "@/types";

interface AppNavItemProps {
  app: App;
  onNavigate?: () => void;
}

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
      onClick={() => {
        navigate(`${appBasePath}/pricing`);
        onNavigate?.();
      }}
    />
  );
}
