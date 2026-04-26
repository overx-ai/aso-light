import { useState } from "react";
import {
  Group,
  Button,
  Modal,
  TextInput,
  Stack,
  Select,
  ActionIcon,
  Text,
  Tooltip,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconDeviceFloppy, IconTrash, IconBookmark } from "@tabler/icons-react";
import { usePresets, useCreatePreset, useDeletePreset } from "@/lib/hooks";
import type { PricePreset, PresetCreate } from "@/types";

interface PresetManagerProps {
  /** Current panel settings to save as a preset. */
  currentSettings: Omit<PresetCreate, "name">;
  /** Called when a preset is selected to load its settings. */
  onLoadPreset: (preset: PricePreset) => void;
}

export default function PresetManager({
  currentSettings,
  onLoadPreset,
}: PresetManagerProps) {
  const [saveOpened, { open: openSave, close: closeSave }] =
    useDisclosure(false);
  const [presetName, setPresetName] = useState("");

  const { data: presets = [] } = usePresets();
  const createMutation = useCreatePreset();
  const deleteMutation = useDeletePreset();

  const presetOptions = presets.map((p: PricePreset) => ({
    value: String(p.id),
    label: p.name,
  }));

  const handleSave = () => {
    if (!presetName.trim()) return;
    createMutation.mutate(
      { name: presetName.trim(), ...currentSettings },
      {
        onSuccess: () => {
          closeSave();
          setPresetName("");
        },
      },
    );
  };

  const handleLoad = (value: string | null) => {
    if (!value) return;
    const preset = presets.find((p: PricePreset) => String(p.id) === value);
    if (preset) {
      onLoadPreset(preset);
    }
  };

  const handleDelete = (id: number, event: React.MouseEvent) => {
    event.stopPropagation();
    deleteMutation.mutate(id);
  };

  return (
    <>
      <Group gap="xs">
        <Select
          placeholder="Load preset..."
          data={presetOptions}
          onChange={handleLoad}
          value={null}
          clearable
          searchable
          size="xs"
          w={180}
          leftSection={<IconBookmark size={14} />}
          rightSectionWidth={presets.length > 0 ? 28 : undefined}
          renderOption={({ option }) => {
            const preset = presets.find(
              (p: PricePreset) => String(p.id) === option.value,
            );
            return (
              <Group justify="space-between" w="100%" wrap="nowrap">
                <Text size="sm" truncate>
                  {option.label}
                </Text>
                {preset && (
                  <Tooltip label="Delete preset" position="right">
                    <ActionIcon
                      size="xs"
                      variant="subtle"
                      color="red"
                      onClick={(e) => handleDelete(preset.id, e)}
                    >
                      <IconTrash size={12} />
                    </ActionIcon>
                  </Tooltip>
                )}
              </Group>
            );
          }}
        />
        <Button
          leftSection={<IconDeviceFloppy size={14} />}
          onClick={openSave}
          variant="light"
          size="xs"
        >
          Save Preset
        </Button>
      </Group>

      <Modal
        opened={saveOpened}
        onClose={closeSave}
        title="Save Price Preset"
        size="sm"
      >
        <Stack>
          <TextInput
            label="Preset Name"
            placeholder="e.g. Standard PPP Pricing"
            value={presetName}
            onChange={(e) => setPresetName(e.currentTarget.value)}
            data-autofocus
          />
          <Text size="xs" c="dimmed">
            Saves the current price configuration (index type, base price, base
            territory, VAT, and charming mode).
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={closeSave}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              loading={createMutation.isPending}
              disabled={!presetName.trim()}
            >
              Save
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
