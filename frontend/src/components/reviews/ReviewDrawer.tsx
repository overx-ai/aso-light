import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Drawer,
  Divider,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconLanguage,
  IconRefresh,
  IconSparkles,
  IconStar,
  IconStarFilled,
  IconTrash,
} from "@tabler/icons-react";
import {
  useCreateReply,
  useDeleteReply,
  useDraftReply,
  useReview,
  useTranslateReview,
  useUpdateReply,
} from "@/lib/hooks";
import {
  REVIEW_THEME_DEFAULT_TONE,
  REVIEW_THEME_OPTIONS,
} from "@/lib/reviewThemes";
import type { ReplyTone, ReviewTheme } from "@/types";

const RESPONSE_BODY_MAX_LEN = 5970;
const SOURCE_LOCALE_STORAGE_KEY = "metadata-source-locale";

interface ReviewDrawerProps {
  appId: number;
  reviewId: string | null;
  opened: boolean;
  onClose: () => void;
}

function StarRow({ rating }: { rating: number }) {
  return (
    <Group gap={2}>
      {[1, 2, 3, 4, 5].map((i) =>
        i <= rating ? (
          <IconStarFilled
            key={i}
            size={16}
            style={{ color: "var(--mantine-color-yellow-6)" }}
          />
        ) : (
          <IconStar key={i} size={16} style={{ color: "var(--mantine-color-gray-4)" }} />
        ),
      )}
    </Group>
  );
}

const TONE_OPTIONS: { value: ReplyTone; label: string }[] = [
  { value: "neutral", label: "Neutral" },
  { value: "apologetic", label: "Apologetic" },
  { value: "appreciative", label: "Appreciative" },
];

export default function ReviewDrawer({
  appId,
  reviewId,
  opened,
  onClose,
}: ReviewDrawerProps) {
  const reviewQuery = useReview(appId, reviewId);
  const draftMutation = useDraftReply(appId);
  const translateMutation = useTranslateReview(appId);
  const createReply = useCreateReply(appId);
  const updateReply = useUpdateReply(appId);
  const deleteReply = useDeleteReply(appId);

  const [tone, setTone] = useState<ReplyTone>("neutral");
  const [theme, setTheme] = useState<ReviewTheme>("other");
  const [reply, setReply] = useState("");
  const [translation, setTranslation] = useState<string | null>(null);
  const [translationCached, setTranslationCached] = useState(false);

  const review = reviewQuery.data;
  const existingResponse = review?.response ?? null;

  const targetLocale = useMemo(() => {
    if (typeof window === "undefined") return "en-US";
    return window.localStorage.getItem(SOURCE_LOCALE_STORAGE_KEY) ?? "en-US";
  }, [opened]);

  // Reset draft & translation when switching reviews / reopening
  useEffect(() => {
    if (!opened) return;
    const nextTheme = review?.theme ?? "other";
    setTheme(nextTheme);
    setTone(REVIEW_THEME_DEFAULT_TONE[nextTheme]);
    setReply(existingResponse?.body ?? "");
    setTranslation(null);
    setTranslationCached(false);
  }, [opened, reviewId, review?.theme, existingResponse?.body]);

  if (!review && reviewQuery.isLoading) {
    return (
      <Drawer opened={opened} onClose={onClose} position="right" size="lg" title="Review">
        <Group justify="center" py="xl">
          <Loader />
        </Group>
      </Drawer>
    );
  }

  if (!review) {
    return (
      <Drawer opened={opened} onClose={onClose} position="right" size="lg" title="Review">
        <Text c="dimmed" size="sm">
          Pick a review to view.
        </Text>
      </Drawer>
    );
  }

  const onTranslate = () => {
    translateMutation.mutate(
      { reviewId: review.id, target_locale: targetLocale },
      {
        onSuccess: (out) => {
          setTranslation(out.translation);
          setTranslationCached(out.cached);
        },
      },
    );
  };

  const onDraft = () => {
    draftMutation.mutate(
      { reviewId: review.id, tone, theme },
      {
        onSuccess: (out) => setReply(out.suggestion),
      },
    );
  };

  const onThemeChange = (value: string | null) => {
    const nextTheme = (value as ReviewTheme | null) ?? "other";
    setTheme(nextTheme);
    setTone(REVIEW_THEME_DEFAULT_TONE[nextTheme]);
  };

  const overLimit = reply.length > RESPONSE_BODY_MAX_LEN;
  const dirty = (existingResponse?.body ?? "") !== reply;

  const onSave = () => {
    if (!dirty || overLimit || reply.length === 0) return;
    if (existingResponse) {
      updateReply.mutate({
        reviewId: review.id,
        responseId: existingResponse.id,
        body: reply,
      });
    } else {
      createReply.mutate({ reviewId: review.id, body: reply });
    }
  };

  const onDelete = () => {
    if (!existingResponse) return;
    deleteReply.mutate({
      reviewId: review.id,
      responseId: existingResponse.id,
    });
  };

  const charsLeft = RESPONSE_BODY_MAX_LEN - reply.length;
  let counterColor: "red" | "yellow" | "green" = "green";
  if (overLimit) counterColor = "red";
  else if (reply.length > RESPONSE_BODY_MAX_LEN * 0.9) counterColor = "yellow";

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size="lg"
      title={
        <Group gap="xs">
          <StarRow rating={review.rating} />
          {review.territory && (
            <Badge size="sm" variant="light" color="gray">
              {review.territory}
            </Badge>
          )}
          {review.reviewer_nickname && (
            <Text size="sm" fw={500}>
              {review.reviewer_nickname}
            </Text>
          )}
        </Group>
      }
    >
      <Stack gap="md">
        <Stack gap={4}>
          {review.title && (
            <Text fw={600} size="sm">
              {review.title}
            </Text>
          )}
          {review.body ? (
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              {review.body}
            </Text>
          ) : (
            <Text size="sm" c="dimmed">
              (no review text)
            </Text>
          )}
          {review.created_date && (
            <Text size="xs" c="dimmed">
              {new Date(review.created_date).toLocaleString()}
            </Text>
          )}
        </Stack>

        {review.body && (
          <>
            <Divider />
            <Group justify="space-between">
              <Text size="xs" fw={600} c="dimmed" tt="uppercase">
                Translate
              </Text>
              <Group gap="xs">
                <Text size="xs" c="dimmed">
                  → {targetLocale}
                </Text>
                <Tooltip label="Translate to your locale" withArrow>
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    onClick={onTranslate}
                    loading={translateMutation.isPending}
                  >
                    <IconLanguage size={14} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Group>
            {translation && (
              <Paper withBorder p="xs">
                <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                  {translation}
                </Text>
                <Text size="xs" c="dimmed" mt={4}>
                  {translationCached ? "from cache" : "fresh"}
                </Text>
              </Paper>
            )}
          </>
        )}

        <Divider />

        <Stack gap="xs">
          <Group justify="space-between">
            <Text size="xs" fw={600} c="dimmed" tt="uppercase">
              Reply
            </Text>
            {existingResponse && (
              <Badge size="xs" color="green" variant="light">
                Already replied
              </Badge>
            )}
          </Group>

          <Group gap="xs">
            <Select
              data={REVIEW_THEME_OPTIONS}
              value={theme}
              onChange={onThemeChange}
              size="xs"
              style={{ flex: 1.2 }}
              label="Theme"
            />
            <Select
              data={TONE_OPTIONS}
              value={tone}
              onChange={(v) => setTone((v as ReplyTone) ?? "neutral")}
              size="xs"
              style={{ flex: 1 }}
              label="Tone"
            />
            <Tooltip label="Suggest reply (Claude)" withArrow>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconSparkles size={12} />}
                onClick={onDraft}
                loading={draftMutation.isPending}
                disabled={!review.body}
              >
                Suggest
              </Button>
            </Tooltip>
          </Group>

          <Textarea
            value={reply}
            onChange={(e) => setReply(e.currentTarget.value)}
            autosize
            minRows={4}
            maxRows={16}
            placeholder="Type your reply here…"
            size="sm"
          />

          <Group justify="space-between" gap="xs">
            <Badge size="xs" variant="light" color={counterColor}>
              {reply.length}/{RESPONSE_BODY_MAX_LEN}{" "}
              {overLimit ? "(over)" : `· ${charsLeft} left`}
            </Badge>
            <Group gap="xs">
              {existingResponse && (
                <Tooltip label="Delete reply" withArrow>
                  <ActionIcon
                    size="md"
                    color="red"
                    variant="subtle"
                    onClick={onDelete}
                    loading={deleteReply.isPending}
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Tooltip>
              )}
              <Button
                size="xs"
                onClick={onSave}
                loading={createReply.isPending || updateReply.isPending}
                disabled={!dirty || overLimit || reply.length === 0}
                leftSection={<IconRefresh size={12} />}
              >
                {existingResponse ? "Update reply" : "Post reply"}
              </Button>
            </Group>
          </Group>

          {overLimit && (
            <Alert color="red" icon={<IconAlertCircle size={14} />} variant="light">
              Reply exceeds Apple's {RESPONSE_BODY_MAX_LEN}-character limit.
            </Alert>
          )}
        </Stack>
      </Stack>
    </Drawer>
  );
}
