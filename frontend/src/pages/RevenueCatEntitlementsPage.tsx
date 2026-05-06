import { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  Container,
  Title,
  Text,
  Stack,
  Group,
  Tabs,
  Paper,
  Button,
  Badge,
  ActionIcon,
  Tooltip,
  Modal,
  TextInput,
  Switch,
  Skeleton,
  Alert,
  Anchor,
  Card,
  Code,
  Divider,
  MultiSelect,
} from "@mantine/core";
import {
  IconArchive,
  IconPlus,
  IconPencil,
  IconLink,
  IconUnlink,
  IconStar,
  IconStarFilled,
  IconAlertCircle,
} from "@tabler/icons-react";
import {
  useRevenueCatCredential,
  useRevenueCatEntitlements,
  useCreateRCEntitlement,
  useUpdateRCEntitlement,
  useArchiveRCEntitlement,
  useAttachProductsToEntitlement,
  useDetachProductsFromEntitlement,
  useRevenueCatOfferings,
  useCreateRCOffering,
  useUpdateRCOffering,
  useArchiveRCOffering,
  useRevenueCatPackages,
  useCreateRCPackage,
  useDeleteRCPackage,
  useAttachProductsToPackage,
  useDetachProductsFromPackage,
  useRevenueCatProducts,
} from "@/lib/hooks";
import type {
  RCEntitlement,
  RCOffering,
  RCPackage,
  RCProduct,
} from "@/types";

// ---------------------------------------------------------------------------
// Entitlements
// ---------------------------------------------------------------------------

function EntitlementsPanel({ appId }: { appId: string }) {
  const entQuery = useRevenueCatEntitlements(appId);
  const productsQuery = useRevenueCatProducts(appId);
  const create = useCreateRCEntitlement();
  const update = useUpdateRCEntitlement();
  const archive = useArchiveRCEntitlement();
  const attach = useAttachProductsToEntitlement();
  const detach = useDetachProductsFromEntitlement();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<RCEntitlement | null>(null);
  const [name, setName] = useState("");
  const [lookupKey, setLookupKey] = useState("");

  const [attachOpen, setAttachOpen] = useState<RCEntitlement | null>(null);
  const [attachIds, setAttachIds] = useState<string[]>([]);

  const productOptions = useMemo(
    () =>
      (productsQuery.data ?? []).map((p) => ({
        value: p.id,
        label: `${p.store_identifier} (${p.id})`,
      })),
    [productsQuery.data],
  );

  const openCreate = () => {
    setEditing(null);
    setName("");
    setLookupKey("");
    setFormOpen(true);
  };
  const openEdit = (ent: RCEntitlement) => {
    setEditing(ent);
    setName(ent.display_name ?? "");
    setLookupKey(ent.lookup_key);
    setFormOpen(true);
  };
  const handleSave = () => {
    if (editing) {
      update.mutate(
        {
          appId,
          entitlementId: editing.id,
          display_name: name,
        },
        { onSuccess: () => setFormOpen(false) },
      );
    } else {
      create.mutate(
        { appId, lookup_key: lookupKey, display_name: name },
        { onSuccess: () => setFormOpen(false) },
      );
    }
  };

  const openAttach = (ent: RCEntitlement) => {
    setAttachOpen(ent);
    setAttachIds([]);
  };
  const handleAttach = () => {
    if (!attachOpen) return;
    attach.mutate(
      {
        appId,
        entitlementId: attachOpen.id,
        product_ids: attachIds,
      },
      { onSuccess: () => setAttachOpen(null) },
    );
  };

  if (entQuery.isLoading) return <Skeleton height={200} />;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Entitlements grant access to features. Attach products to grant the
          entitlement when those products are purchased.
        </Text>
        <Button
          leftSection={<IconPlus size={16} />}
          size="sm"
          onClick={openCreate}
        >
          New entitlement
        </Button>
      </Group>

      {(entQuery.data ?? []).length === 0 ? (
        <Paper withBorder p="lg" ta="center">
          <Text c="dimmed">No entitlements yet.</Text>
        </Paper>
      ) : (
        <Stack gap="sm">
          {(entQuery.data ?? []).map((ent) => (
            <Card key={ent.id} withBorder p="md" radius="sm">
              <Group justify="space-between" align="flex-start">
                <Stack gap={2}>
                  <Group gap="xs">
                    <Text fw={600}>{ent.display_name ?? ent.lookup_key}</Text>
                    <Code>{ent.lookup_key}</Code>
                    {ent.is_archived ? (
                      <Badge size="xs" color="gray">
                        archived
                      </Badge>
                    ) : null}
                  </Group>
                  <Text size="xs" c="dimmed">
                    Products:{" "}
                    {(ent.products ?? []).length === 0
                      ? "none attached"
                      : (ent.products ?? [])
                          .map((p) => p.store_identifier ?? p.id)
                          .join(", ")}
                  </Text>
                </Stack>
                <Group gap="xs">
                  <Tooltip label="Edit display name">
                    <ActionIcon
                      variant="subtle"
                      onClick={() => openEdit(ent)}
                    >
                      <IconPencil size={16} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Attach products">
                    <ActionIcon
                      variant="subtle"
                      color="blue"
                      onClick={() => openAttach(ent)}
                    >
                      <IconLink size={16} />
                    </ActionIcon>
                  </Tooltip>
                  {(ent.products ?? []).map((p) => (
                    <Tooltip key={p.id} label={`Detach ${p.store_identifier}`}>
                      <ActionIcon
                        variant="subtle"
                        color="orange"
                        onClick={() =>
                          detach.mutate({
                            appId,
                            entitlementId: ent.id,
                            product_ids: [p.id ?? ""],
                          })
                        }
                      >
                        <IconUnlink size={16} />
                      </ActionIcon>
                    </Tooltip>
                  ))}
                  {!ent.is_archived ? (
                    <Tooltip label="Archive">
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={() =>
                          archive.mutate({
                            appId,
                            entitlementId: ent.id,
                          })
                        }
                      >
                        <IconArchive size={16} />
                      </ActionIcon>
                    </Tooltip>
                  ) : null}
                </Group>
              </Group>
            </Card>
          ))}
        </Stack>
      )}

      <Modal
        opened={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? "Edit entitlement" : "New entitlement"}
        centered
      >
        <Stack gap="md">
          <TextInput
            label="Lookup key"
            description="Stable identifier used in app code. Cannot be changed later."
            value={lookupKey}
            onChange={(e) => setLookupKey(e.currentTarget.value)}
            disabled={!!editing}
            required
          />
          <TextInput
            label="Display name"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            required
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setFormOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              loading={create.isPending || update.isPending}
            >
              Save
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={!!attachOpen}
        onClose={() => setAttachOpen(null)}
        title={`Attach products to ${attachOpen?.lookup_key ?? ""}`}
        centered
      >
        <Stack gap="md">
          <MultiSelect
            label="Products"
            data={productOptions}
            value={attachIds}
            onChange={setAttachIds}
            searchable
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setAttachOpen(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleAttach}
              loading={attach.isPending}
              disabled={attachIds.length === 0}
            >
              Attach
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Offerings + Packages
// ---------------------------------------------------------------------------

function PackageRow({
  appId,
  offeringId,
  pkg,
  productOptions,
}: {
  appId: string;
  offeringId: string;
  pkg: RCPackage;
  productOptions: { value: string; label: string }[];
}) {
  const attach = useAttachProductsToPackage();
  const detach = useDetachProductsFromPackage();
  const remove = useDeleteRCPackage();
  const [attachOpen, setAttachOpen] = useState(false);
  const [attachIds, setAttachIds] = useState<string[]>([]);

  return (
    <Card withBorder p="sm">
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Group gap="xs">
            <Text fw={500}>{pkg.display_name ?? pkg.lookup_key}</Text>
            <Code>{pkg.lookup_key}</Code>
          </Group>
          <Text size="xs" c="dimmed">
            Products:{" "}
            {(pkg.products ?? []).length === 0
              ? "none"
              : (pkg.products ?? [])
                  .map((p) => p.store_identifier ?? p.id)
                  .join(", ")}
          </Text>
        </Stack>
        <Group gap="xs">
          <Tooltip label="Attach products">
            <ActionIcon
              variant="subtle"
              color="blue"
              onClick={() => setAttachOpen(true)}
            >
              <IconLink size={16} />
            </ActionIcon>
          </Tooltip>
          {(pkg.products ?? []).map((p) => (
            <Tooltip key={p.id} label={`Detach ${p.store_identifier}`}>
              <ActionIcon
                variant="subtle"
                color="orange"
                onClick={() =>
                  detach.mutate({
                    appId,
                    offeringId,
                    packageId: pkg.id,
                    product_ids: [p.id ?? ""],
                  })
                }
              >
                <IconUnlink size={16} />
              </ActionIcon>
            </Tooltip>
          ))}
          <Tooltip label="Delete package">
            <ActionIcon
              variant="subtle"
              color="red"
              onClick={() => {
                if (!confirm(`Delete package ${pkg.lookup_key}?`)) return;
                remove.mutate({
                  appId,
                  offeringId,
                  packageId: pkg.id,
                });
              }}
            >
              <IconArchive size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      <Modal
        opened={attachOpen}
        onClose={() => setAttachOpen(false)}
        title={`Attach products to ${pkg.lookup_key}`}
        centered
      >
        <Stack gap="md">
          <MultiSelect
            label="Products"
            data={productOptions}
            value={attachIds}
            onChange={setAttachIds}
            searchable
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setAttachOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                attach.mutate(
                  {
                    appId,
                    offeringId,
                    packageId: pkg.id,
                    product_ids: attachIds,
                  },
                  {
                    onSuccess: () => {
                      setAttachOpen(false);
                      setAttachIds([]);
                    },
                  },
                )
              }
              loading={attach.isPending}
              disabled={attachIds.length === 0}
            >
              Attach
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

function OfferingCard({
  appId,
  offering,
  productOptions,
}: {
  appId: string;
  offering: RCOffering;
  productOptions: { value: string; label: string }[];
}) {
  const packagesQuery = useRevenueCatPackages(appId, offering.id);
  const createPkg = useCreateRCPackage();
  const update = useUpdateRCOffering();

  const [pkgOpen, setPkgOpen] = useState(false);
  const [pkgKey, setPkgKey] = useState("");
  const [pkgName, setPkgName] = useState("");

  const handleCreatePkg = () =>
    createPkg.mutate(
      {
        appId,
        offeringId: offering.id,
        lookup_key: pkgKey,
        display_name: pkgName,
      },
      {
        onSuccess: () => {
          setPkgOpen(false);
          setPkgKey("");
          setPkgName("");
        },
      },
    );

  return (
    <Card withBorder p="md" radius="sm">
      <Group justify="space-between">
        <Group gap="xs">
          <Text fw={600}>{offering.display_name ?? offering.lookup_key}</Text>
          <Code>{offering.lookup_key}</Code>
          {offering.is_current ? (
            <Badge color="green" variant="light">
              current
            </Badge>
          ) : null}
          {offering.is_archived ? (
            <Badge color="gray" variant="light">
              archived
            </Badge>
          ) : null}
        </Group>
        <Group gap="xs">
          <Tooltip
            label={offering.is_current ? "Current offering" : "Make current"}
          >
            <ActionIcon
              variant="subtle"
              color={offering.is_current ? "yellow" : "gray"}
              onClick={() =>
                update.mutate({
                  appId,
                  offeringId: offering.id,
                  is_current: true,
                })
              }
              disabled={offering.is_current ?? false}
            >
              {offering.is_current ? (
                <IconStarFilled size={16} />
              ) : (
                <IconStar size={16} />
              )}
            </ActionIcon>
          </Tooltip>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconPlus size={14} />}
            onClick={() => setPkgOpen(true)}
          >
            Package
          </Button>
        </Group>
      </Group>
      <Divider my="sm" />
      {packagesQuery.isLoading ? (
        <Skeleton height={60} />
      ) : (packagesQuery.data ?? []).length === 0 ? (
        <Text size="sm" c="dimmed">
          No packages yet.
        </Text>
      ) : (
        <Stack gap="xs">
          {(packagesQuery.data ?? []).map((pkg) => (
            <PackageRow
              key={pkg.id}
              appId={appId}
              offeringId={offering.id}
              pkg={pkg}
              productOptions={productOptions}
            />
          ))}
        </Stack>
      )}
      <Modal
        opened={pkgOpen}
        onClose={() => setPkgOpen(false)}
        title="New package"
        centered
      >
        <Stack gap="md">
          <TextInput
            label="Lookup key"
            description="e.g. monthly, annual, weekly"
            value={pkgKey}
            onChange={(e) => setPkgKey(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Display name"
            value={pkgName}
            onChange={(e) => setPkgName(e.currentTarget.value)}
            required
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setPkgOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreatePkg}
              loading={createPkg.isPending}
              disabled={!pkgKey || !pkgName}
            >
              Create
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

function OfferingsPanel({ appId }: { appId: string }) {
  const offQuery = useRevenueCatOfferings(appId);
  const productsQuery = useRevenueCatProducts(appId);
  const create = useCreateRCOffering();
  const archive = useArchiveRCOffering();

  const [createOpen, setCreateOpen] = useState(false);
  const [lookupKey, setLookupKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [isCurrent, setIsCurrent] = useState(false);

  const productOptions = useMemo(
    () =>
      (productsQuery.data ?? []).map((p) => ({
        value: p.id,
        label: `${p.store_identifier} (${p.id})`,
      })),
    [productsQuery.data],
  );

  const handleCreate = () =>
    create.mutate(
      {
        appId,
        lookup_key: lookupKey,
        display_name: displayName,
        is_current: isCurrent,
      },
      {
        onSuccess: () => {
          setCreateOpen(false);
          setLookupKey("");
          setDisplayName("");
          setIsCurrent(false);
        },
      },
    );

  if (offQuery.isLoading) return <Skeleton height={200} />;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Offerings group packages of products that the app surfaces in a
          paywall. Mark one as current — the SDK fetches it by default.
        </Text>
        <Button
          leftSection={<IconPlus size={16} />}
          size="sm"
          onClick={() => setCreateOpen(true)}
        >
          New offering
        </Button>
      </Group>

      {(offQuery.data ?? []).length === 0 ? (
        <Paper withBorder p="lg" ta="center">
          <Text c="dimmed">No offerings yet.</Text>
        </Paper>
      ) : (
        <Stack gap="md">
          {(offQuery.data ?? []).map((off) => (
            <Stack key={off.id} gap="xs">
              <OfferingCard
                appId={appId}
                offering={off}
                productOptions={productOptions}
              />
              {!off.is_archived ? (
                <Group justify="flex-end">
                  <Button
                    size="xs"
                    color="red"
                    variant="subtle"
                    leftSection={<IconArchive size={14} />}
                    onClick={() =>
                      archive.mutate({ appId, offeringId: off.id })
                    }
                  >
                    Archive offering
                  </Button>
                </Group>
              ) : null}
            </Stack>
          ))}
        </Stack>
      )}

      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title="New offering"
        centered
      >
        <Stack gap="md">
          <TextInput
            label="Lookup key"
            value={lookupKey}
            onChange={(e) => setLookupKey(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.currentTarget.value)}
            required
          />
          <Switch
            label="Make this the current offering"
            checked={isCurrent}
            onChange={(e) => setIsCurrent(e.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              loading={create.isPending}
              disabled={!lookupKey || !displayName}
            >
              Create
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Products (read-only mirror)
// ---------------------------------------------------------------------------

function ProductsPanel({ appId }: { appId: string }) {
  const productsQuery = useRevenueCatProducts(appId);
  if (productsQuery.isLoading) return <Skeleton height={200} />;
  const products: RCProduct[] = productsQuery.data ?? [];
  return (
    <Stack gap="md">
      <Text size="sm" c="dimmed">
        Products are mirrored from RevenueCat. Use the &ldquo;Clone &amp;
        version-bump&rdquo; action on the Pricing page to mint new
        productIds in ASC and have them auto-attached here.
      </Text>
      {products.length === 0 ? (
        <Paper withBorder p="lg" ta="center">
          <Text c="dimmed">
            No products visible. Make sure the secret key has read access and
            an app is linked.
          </Text>
        </Paper>
      ) : (
        <Stack gap="xs">
          {products.map((p) => (
            <Card key={p.id} withBorder p="sm">
              <Group justify="space-between">
                <Stack gap={2}>
                  <Text fw={500}>
                    {p.display_name ?? p.store_identifier}
                  </Text>
                  <Group gap="xs">
                    <Code>{p.store_identifier}</Code>
                    {p.type ? (
                      <Badge variant="light" size="xs">
                        {p.type}
                      </Badge>
                    ) : null}
                    {p.is_archived ? (
                      <Badge color="gray" variant="light" size="xs">
                        archived
                      </Badge>
                    ) : null}
                  </Group>
                </Stack>
                <Code c="dimmed" fz="xs">
                  {p.id}
                </Code>
              </Group>
            </Card>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RevenueCatEntitlementsPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ?? "";
  const credQuery = useRevenueCatCredential(appId);

  if (credQuery.isLoading) {
    return (
      <Container size="lg" py="md">
        <Skeleton height={200} />
      </Container>
    );
  }

  if (!credQuery.data) {
    return (
      <Container size="md" py="md">
        <Alert
          icon={<IconAlertCircle size={16} />}
          color="orange"
          title="RevenueCat not connected"
        >
          <Stack gap="xs">
            <Text size="sm">
              Connect this app to a RevenueCat project before managing
              entitlements.
            </Text>
            <Anchor
              component={Link}
              to={`/apps/${appId}/revenuecat/settings`}
              size="sm"
            >
              Open RevenueCat settings →
            </Anchor>
          </Stack>
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <Group justify="space-between" align="flex-end">
          <div>
            <Title order={2}>RevenueCat</Title>
            <Text c="dimmed" size="sm">
              Project <Code>{credQuery.data.project_id}</Code>
              {credQuery.data.rc_app_id ? (
                <>
                  {" "}
                  · App <Code>{credQuery.data.rc_app_id}</Code>
                </>
              ) : null}
            </Text>
          </div>
          <Anchor
            component={Link}
            to={`/apps/${appId}/revenuecat/settings`}
            size="sm"
          >
            Settings →
          </Anchor>
        </Group>

        <Tabs defaultValue="entitlements">
          <Tabs.List>
            <Tabs.Tab value="entitlements">Entitlements</Tabs.Tab>
            <Tabs.Tab value="offerings">Offerings &amp; Packages</Tabs.Tab>
            <Tabs.Tab value="products">Products</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="entitlements" pt="md">
            <EntitlementsPanel appId={appId} />
          </Tabs.Panel>
          <Tabs.Panel value="offerings" pt="md">
            <OfferingsPanel appId={appId} />
          </Tabs.Panel>
          <Tabs.Panel value="products" pt="md">
            <ProductsPanel appId={appId} />
          </Tabs.Panel>
        </Tabs>
      </Stack>
    </Container>
  );
}
