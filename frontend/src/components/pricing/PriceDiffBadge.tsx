import { Badge } from "@mantine/core";
import { IconArrowUp, IconArrowDown, IconMinus } from "@tabler/icons-react";

interface PriceDiffBadgeProps {
  diffPercent: number | null;
}

export default function PriceDiffBadge({ diffPercent }: PriceDiffBadgeProps) {
  if (diffPercent === null || diffPercent === undefined) {
    return (
      <Badge variant="light" color="gray" size="sm" leftSection={<IconMinus size={10} />}>
        N/A
      </Badge>
    );
  }

  if (Math.abs(diffPercent) < 0.01) {
    return (
      <Badge variant="light" color="gray" size="sm" leftSection={<IconMinus size={10} />}>
        0%
      </Badge>
    );
  }

  const isIncrease = diffPercent > 0;

  return (
    <Badge
      variant="light"
      color={isIncrease ? "red" : "green"}
      size="sm"
      leftSection={
        isIncrease ? <IconArrowUp size={10} /> : <IconArrowDown size={10} />
      }
    >
      {isIncrease ? "+" : ""}
      {diffPercent.toFixed(1)}%
    </Badge>
  );
}
