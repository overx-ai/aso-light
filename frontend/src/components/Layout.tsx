import { useState } from "react";
import { Outlet, useNavigate, useLocation, useParams } from "react-router-dom";
import {
  AppShell,
  Burger,
  Group,
  NavLink,
  Text,
  Menu,
  UnstyledButton,
  Avatar,
  Divider,
  Skeleton,
  Stack,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconDashboard,
  IconKey,
  IconSettings,
  IconLogout,
  IconUser,
  IconApps,
} from "@tabler/icons-react";
import { useAuth } from "@/lib/auth";
import { useApps } from "@/lib/hooks";
import AppNavItem from "@/components/AppNavItem";

const NAV_ITEMS = [
  { label: "Dashboard", icon: IconDashboard, path: "/" },
  { label: "Credentials", icon: IconKey, path: "/credentials" },
  { label: "Settings", icon: IconSettings, path: "/settings" },
];

function useCurrentAppName(): string | undefined {
  const { id } = useParams<{ id: string }>();
  const { data: apps } = useApps();
  if (!id || !apps) return undefined;
  const app = apps.find((a) => String(a.id) === id);
  return app?.name;
}

function HeaderTitle() {
  const appName = useCurrentAppName();
  return (
    <Text size="lg" fw={700}>
      {appName ? `ASO Light - ${appName}` : "ASO Light"}
    </Text>
  );
}

export default function Layout() {
  const [opened, { toggle }] = useDisclosure();
  const [menuOpened, setMenuOpened] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { data: apps, isLoading: appsLoading } = useApps();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{
        width: 250,
        breakpoint: "sm",
        collapsed: { mobile: !opened },
      }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger
              opened={opened}
              onClick={toggle}
              hiddenFrom="sm"
              size="sm"
            />
            <HeaderTitle />
          </Group>

          <Menu
            opened={menuOpened}
            onChange={setMenuOpened}
            position="bottom-end"
          >
            <Menu.Target>
              <UnstyledButton>
                <Group gap="xs">
                  <Avatar size="sm" radius="xl" color="blue">
                    <IconUser size={16} />
                  </Avatar>
                  <Text size="sm" visibleFrom="sm">
                    {user?.name ?? user?.email}
                  </Text>
                </Group>
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{user?.email}</Menu.Label>
              <Menu.Divider />
              <Menu.Item
                leftSection={<IconLogout size={14} />}
                onClick={handleLogout}
              >
                Logout
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            label={item.label}
            leftSection={<item.icon size={20} />}
            active={location.pathname === item.path}
            onClick={() => {
              navigate(item.path);
              toggle();
            }}
          />
        ))}

        <Divider my="sm" />

        <Group gap="xs" px="sm" mb="xs">
          <IconApps size={14} color="var(--mantine-color-dimmed)" />
          <Text size="xs" fw={600} c="dimmed" tt="uppercase">
            Your Apps
          </Text>
        </Group>

        {appsLoading ? (
          <Stack gap="xs" px="sm">
            <Skeleton height={32} />
            <Skeleton height={32} />
            <Skeleton height={32} />
          </Stack>
        ) : !apps || apps.length === 0 ? (
          <Text size="xs" c="dimmed" px="sm">
            No apps synced yet.
          </Text>
        ) : (
          apps.map((app) => (
            <AppNavItem key={app.id} app={app} onNavigate={toggle} />
          ))
        )}
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
