import { describe, expect, it } from "vitest";
import type { ASAAdGroupOut, ASAPerformanceReportRow } from "@/lib/hooks";
import {
  buildCampaignDrilldown,
  buildPerformanceMap,
} from "./campaignDrilldown";

function makePerformanceRow(
  overrides: Partial<ASAPerformanceReportRow>,
): ASAPerformanceReportRow {
  return {
    dim_kind: "AD_GROUP",
    dim_id: 1,
    app_adam_id: "123456789",
    date: "2026-05-20",
    storefront: null,
    impressions: 0,
    taps: 0,
    installs: 0,
    new_downloads: 0,
    redownloads: 0,
    spend_amount: "0",
    spend_currency: "USD",
    avg_cpa_amount: null,
    avg_cpt_amount: null,
    ttr: null,
    conversion_rate: null,
    ...overrides,
  };
}

function makeAdGroup(overrides: Partial<ASAAdGroupOut>): ASAAdGroupOut {
  return {
    id: 1,
    campaign_id: 10,
    asa_ad_group_id: 101,
    name: "Core",
    status: "ENABLED",
    default_bid_amount: null,
    default_bid_currency: null,
    age_range: null,
    gender: null,
    device_class: null,
    archived_at: null,
    ...overrides,
  };
}

describe("buildPerformanceMap", () => {
  it("aggregates rows by dimension id and recomputes derived rates", () => {
    const metrics = buildPerformanceMap([
      makePerformanceRow({
        dim_id: 11,
        impressions: 120,
        taps: 12,
        installs: 4,
        spend_amount: "24.00",
      }),
      makePerformanceRow({
        dim_id: 11,
        impressions: 30,
        taps: 3,
        installs: 1,
        spend_amount: "6.00",
      }),
      makePerformanceRow({
        dim_id: 12,
        impressions: 40,
        taps: 8,
        installs: 2,
        spend_amount: "12.50",
      }),
    ]);

    expect(metrics.get(11)).toMatchObject({
      impressions: 150,
      taps: 15,
      installs: 5,
      spend: 30,
      spendCurrency: "USD",
    });
    expect(metrics.get(11)?.avgCpt).toBeCloseTo(2);
    expect(metrics.get(11)?.avgCpa).toBeCloseTo(6);
    expect(metrics.get(11)?.ttr).toBeCloseTo(0.1);
    expect(metrics.get(11)?.conversionRate).toBeCloseTo(5 / 15);
  });
});

describe("buildCampaignDrilldown", () => {
  it("keeps the selected campaign's ad groups, includes zero rows, and sorts by spend", () => {
    const adGroups = [
      makeAdGroup({
        id: 11,
        name: "Brand",
        default_bid_amount: "2.25",
        default_bid_currency: "USD",
      }),
      makeAdGroup({
        id: 12,
        name: "Competitor",
        status: "PAUSED",
      }),
    ];

    const drilldown = buildCampaignDrilldown(adGroups, [
      makePerformanceRow({
        dim_id: 99,
        impressions: 200,
        taps: 20,
        installs: 4,
        spend_amount: "50.00",
      }),
      makePerformanceRow({
        dim_id: 11,
        impressions: 80,
        taps: 10,
        installs: 3,
        spend_amount: "18.00",
      }),
      makePerformanceRow({
        dim_id: 11,
        impressions: 20,
        taps: 2,
        installs: 1,
        spend_amount: "6.00",
      }),
    ]);

    expect(drilldown.rows.map((row) => row.id)).toEqual([11, 12]);
    expect(drilldown.rows[0]).toMatchObject({
      name: "Brand",
      taps: 12,
      installs: 4,
      spend: 24,
      defaultBidAmount: "2.25",
      defaultBidCurrency: "USD",
    });
    expect(drilldown.rows[1]).toMatchObject({
      name: "Competitor",
      status: "PAUSED",
      impressions: 0,
      taps: 0,
      installs: 0,
      spend: 0,
    });
    expect(drilldown.totals).toMatchObject({
      impressions: 100,
      taps: 12,
      installs: 4,
      spend: 24,
      spendCurrency: "USD",
    });
  });
});
