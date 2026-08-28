import type { ASAAdGroupOut, ASAPerformanceReportRow } from "@/lib/hooks";

export interface AggregatedPerformance {
  impressions: number;
  taps: number;
  installs: number;
  spend: number;
  spendCurrency: string | null;
  avgCpt: number | null;
  avgCpa: number | null;
  ttr: number | null;
  conversionRate: number | null;
}

export interface CampaignDrilldownRow extends AggregatedPerformance {
  id: number;
  asaAdGroupId: number;
  name: string;
  status: string;
  defaultBidAmount: string | null;
  defaultBidCurrency: string | null;
  deviceClass: string | null;
  gender: string | null;
  archivedAt: string | null;
}

export interface CampaignDrilldownData {
  totals: AggregatedPerformance;
  rows: CampaignDrilldownRow[];
}

interface MutablePerformanceTotals {
  impressions: number;
  taps: number;
  installs: number;
  spend: number;
  spendCurrency: string | null;
}

function parseSpend(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function derivePerformance(
  totals: MutablePerformanceTotals,
): AggregatedPerformance {
  return {
    ...totals,
    avgCpt: totals.taps > 0 ? totals.spend / totals.taps : null,
    avgCpa: totals.installs > 0 ? totals.spend / totals.installs : null,
    ttr: totals.impressions > 0 ? totals.taps / totals.impressions : null,
    conversionRate: totals.taps > 0 ? totals.installs / totals.taps : null,
  };
}

function emptyPerformance(spendCurrency: string | null = null): AggregatedPerformance {
  return derivePerformance({
    impressions: 0,
    taps: 0,
    installs: 0,
    spend: 0,
    spendCurrency,
  });
}

export function buildPerformanceMap(
  rows: ASAPerformanceReportRow[],
): Map<number, AggregatedPerformance> {
  const totalsByDimId = new Map<number, MutablePerformanceTotals>();

  for (const row of rows) {
    const totals = totalsByDimId.get(row.dim_id) ?? {
      impressions: 0,
      taps: 0,
      installs: 0,
      spend: 0,
      spendCurrency: null,
    };

    totals.impressions += row.impressions;
    totals.taps += row.taps;
    totals.installs += row.installs;
    totals.spend += parseSpend(row.spend_amount);
    totals.spendCurrency = totals.spendCurrency ?? row.spend_currency;

    totalsByDimId.set(row.dim_id, totals);
  }

  return new Map(
    Array.from(totalsByDimId.entries()).map(([dimId, totals]) => [
      dimId,
      derivePerformance(totals),
    ]),
  );
}

export function buildCampaignDrilldown(
  adGroups: ASAAdGroupOut[],
  performanceRows: ASAPerformanceReportRow[],
): CampaignDrilldownData {
  const performanceByAdGroupId = buildPerformanceMap(performanceRows);

  const rows = adGroups
    .map((adGroup) => {
      const metrics =
        performanceByAdGroupId.get(adGroup.id) ??
        emptyPerformance(adGroup.default_bid_currency);

      return {
        id: adGroup.id,
        asaAdGroupId: adGroup.asa_ad_group_id,
        name: adGroup.name,
        status: adGroup.status,
        defaultBidAmount: adGroup.default_bid_amount,
        defaultBidCurrency: adGroup.default_bid_currency,
        deviceClass: adGroup.device_class,
        gender: adGroup.gender,
        archivedAt: adGroup.archived_at,
        ...metrics,
      };
    })
    .sort((a, b) => {
      if (b.spend !== a.spend) return b.spend - a.spend;
      if (b.installs !== a.installs) return b.installs - a.installs;
      if (b.taps !== a.taps) return b.taps - a.taps;
      return a.name.localeCompare(b.name);
    });

  const totals = derivePerformance(
    rows.reduce<MutablePerformanceTotals>(
      (acc, row) => {
        acc.impressions += row.impressions;
        acc.taps += row.taps;
        acc.installs += row.installs;
        acc.spend += row.spend;
        acc.spendCurrency = acc.spendCurrency ?? row.spendCurrency;
        return acc;
      },
      {
        impressions: 0,
        taps: 0,
        installs: 0,
        spend: 0,
        spendCurrency: null,
      },
    ),
  );

  return { totals, rows };
}
