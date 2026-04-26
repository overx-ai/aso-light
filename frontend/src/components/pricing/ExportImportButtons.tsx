import { useRef } from "react";
import { Group, Button, Menu } from "@mantine/core";
import {
  IconDownload,
  IconUpload,
  IconFileSpreadsheet,
  IconFileText,
} from "@tabler/icons-react";
import { useExportPrices, useImportPrices } from "@/lib/hooks";
import type { PriceExportItem, PriceImportItem } from "@/types";

interface ExportImportButtonsProps {
  /** Name used for the exported file. */
  subscriptionName: string;
  /** Price data to export. */
  prices: PriceExportItem[];
  /** Called when prices are imported from a file. */
  onImport: (items: PriceImportItem[]) => void;
}

export default function ExportImportButtons({
  subscriptionName,
  prices,
  onImport,
}: ExportImportButtonsProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const exportMutation = useExportPrices();
  const importMutation = useImportPrices();

  const handleExport = (format: "xlsx" | "csv") => {
    if (prices.length === 0) return;
    exportMutation.mutate({ subscriptionName, format, prices });
  };

  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    importMutation.mutate(file, {
      onSuccess: (data) => {
        onImport(data.items);
      },
    });

    // Reset input so the same file can be selected again
    event.target.value = "";
  };

  const isExportDisabled = prices.length === 0;

  return (
    <Group gap="xs">
      <Menu shadow="md" width={180}>
        <Menu.Target>
          <Button
            leftSection={<IconDownload size={14} />}
            variant="light"
            size="xs"
            disabled={isExportDisabled}
            loading={exportMutation.isPending}
          >
            Export
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item
            leftSection={<IconFileSpreadsheet size={14} />}
            onClick={() => handleExport("xlsx")}
          >
            Export as Excel
          </Menu.Item>
          <Menu.Item
            leftSection={<IconFileText size={14} />}
            onClick={() => handleExport("csv")}
          >
            Export as CSV
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>

      <Button
        leftSection={<IconUpload size={14} />}
        variant="light"
        size="xs"
        onClick={handleImportClick}
        loading={importMutation.isPending}
      >
        Import
      </Button>

      <input
        ref={fileInputRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        onChange={handleFileChange}
        style={{ display: "none" }}
      />
    </Group>
  );
}
