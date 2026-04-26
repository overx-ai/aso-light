import { useRef } from "react";
import { Paper, Group, Stack, Text, Image, Button, FileButton } from "@mantine/core";
import { IconPhoto, IconUpload } from "@tabler/icons-react";
import type { ReviewScreenshot } from "@/types";

interface ReviewScreenshotUploadProps {
  screenshot: ReviewScreenshot | null | undefined;
  onUpload: (file: File) => void;
  isUploading: boolean;
  isLoading: boolean;
}

export default function ReviewScreenshotUpload({
  screenshot,
  onUpload,
  isUploading,
  isLoading,
}: ReviewScreenshotUploadProps) {
  const resetRef = useRef<() => void>(null);

  const handleFile = (file: File | null) => {
    if (file) {
      onUpload(file);
      resetRef.current?.();
    }
  };

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="sm">
        <Group gap="xs">
          <IconPhoto size={16} color="var(--mantine-color-blue-6)" />
          <Text fw={600} size="sm">
            Review Screenshot
          </Text>
        </Group>

        <Group align="flex-start" gap="md">
          {screenshot?.image_url ? (
            <Image
              src={screenshot.image_url}
              alt="Review screenshot"
              w={120}
              h={172}
              radius="sm"
              fit="contain"
              style={{ border: "1px solid var(--mantine-color-gray-3)" }}
            />
          ) : (
            <div
              style={{
                width: 120,
                height: 172,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                border: "2px dashed var(--mantine-color-gray-3)",
                borderRadius: "var(--mantine-radius-sm)",
                color: "var(--mantine-color-dimmed)",
              }}
            >
              <Stack align="center" gap={4}>
                <IconPhoto size={32} />
                <Text size="xs">No screenshot</Text>
              </Stack>
            </div>
          )}

          <Stack gap="xs">
            {screenshot && (
              <Text size="xs" c="dimmed">
                {screenshot.file_name} ({Math.round(screenshot.file_size / 1024)}KB)
              </Text>
            )}
            <FileButton
              onChange={handleFile}
              accept="image/png,image/jpeg"
              resetRef={resetRef}
            >
              {(props) => (
                <Button
                  {...props}
                  size="xs"
                  variant="light"
                  leftSection={<IconUpload size={14} />}
                  loading={isUploading || isLoading}
                >
                  {screenshot ? "Replace" : "Upload"}
                </Button>
              )}
            </FileButton>
            <Text size="xs" c="dimmed">
              PNG or JPEG, recommended 640x920
            </Text>
          </Stack>
        </Group>
      </Stack>
    </Paper>
  );
}
