import { Badge } from "@mantine/core";

interface CharLimitCounterProps {
  value: string;
  limit: number | null;
}

/**
 * Compact counter showing N/limit with traffic-light colouring:
 *  green   ≤ 90%
 *  yellow  > 90% and ≤ 100%
 *  red     > 100%
 */
export default function CharLimitCounter({ value, limit }: CharLimitCounterProps) {
  if (limit == null) return null;
  const used = value.length;
  let color: "green" | "yellow" | "red" = "green";
  if (used > limit) color = "red";
  else if (used > limit * 0.9) color = "yellow";

  return (
    <Badge size="sm" variant="light" color={color}>
      {used}/{limit}
    </Badge>
  );
}
