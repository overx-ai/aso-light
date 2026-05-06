import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Center,
  Container,
  Loader,
  Stack,
  Tabs,
} from "@mantine/core";
import { IconAlertCircle, IconLanguage, IconList } from "@tabler/icons-react";
import {
  useApp,
  useAppMetadata,
  useSyncMetadata,
} from "@/lib/hooks";
import MetadataHeader from "@/components/metadata/MetadataHeader";
import LocaleEditor from "@/components/metadata/LocaleEditor";
import MetadataGrid from "@/components/metadata/MetadataGrid";
import BulkFanoutDrawer from "@/components/metadata/BulkFanoutDrawer";
import EmptyState from "@/components/metadata/EmptyState";

type ActiveTab = "single" | "grid";

export default function MetadataPage() {
  const { id } = useParams<{ id: string }>();
  const appId = Number(id);

  // useApp() expects a string; we coerce on the way in to keep the rest of
  // the metadata hooks numeric (matches their declared signatures).
  const { data: app } = useApp(id ?? "");
  const { data: snapshot, isLoading, error } = useAppMetadata(appId);
  const sync = useSyncMetadata();

  const [activeTab, setActiveTab] = useState<ActiveTab>("single");
  const [selectedLocale, setSelectedLocale] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  if (isLoading) {
    return (
      <Container size="xl">
        <Center mih={200}>
          <Loader />
        </Center>
      </Container>
    );
  }

  if (error) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />} title="Failed to load metadata">
          {(error as Error).message ?? "Unknown error"}
        </Alert>
      </Container>
    );
  }

  if (snapshot == null) {
    return (
      <Container size="xl">
        <EmptyState
          onSync={() => sync.mutate(appId)}
          loading={sync.isPending}
        />
      </Container>
    );
  }

  return (
    <Container size="xl">
      <Stack gap="md">
        <MetadataHeader
          app={app}
          state={snapshot.state}
          syncing={sync.isPending}
          onSync={() => sync.mutate(appId)}
        />
        <Tabs
          value={activeTab}
          onChange={(v) => setActiveTab((v as ActiveTab) ?? "single")}
        >
          <Tabs.List>
            <Tabs.Tab value="single" leftSection={<IconLanguage size={16} />}>
              Single locale
            </Tabs.Tab>
            <Tabs.Tab value="grid" leftSection={<IconList size={16} />}>
              All locales (grid)
            </Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="single">
            <LocaleEditor
              appId={appId}
              snapshot={snapshot}
              selectedLocale={selectedLocale}
              onSelectLocale={setSelectedLocale}
            />
          </Tabs.Panel>
          <Tabs.Panel value="grid">
            <MetadataGrid
              appId={appId}
              snapshot={snapshot}
              onRowClick={(loc) => {
                setSelectedLocale(loc);
                setActiveTab("single");
              }}
              onOpenBulk={() => setBulkOpen(true)}
            />
          </Tabs.Panel>
        </Tabs>
        <BulkFanoutDrawer
          appId={appId}
          snapshot={snapshot}
          opened={bulkOpen}
          onClose={() => setBulkOpen(false)}
        />
      </Stack>
    </Container>
  );
}
