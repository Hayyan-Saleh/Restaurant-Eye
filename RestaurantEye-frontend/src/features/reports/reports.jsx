import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import SectionCard from "@/features/dashboard/components/SectionCard";
import EmptyState from "@/features/dashboard/components/EmptyState";
import { BarChart2 } from "lucide-react";
import { useHistoricalKpis } from "@/hooks/usehistoricalkpis";

const ALERT_TYPE_COLORS = {
  DELAY_ALERT: "#f87171",
  WORKER_IDLE_TOO_LONG: "#fbbf24",
};
const FALLBACK_COLORS = ["#60a5fa", "#34d399", "#a78bfa", "#f472b6"];

function colorFor(alertType, index) {
  return (
    ALERT_TYPE_COLORS[alertType] ??
    FALLBACK_COLORS[index % FALLBACK_COLORS.length]
  );
}

function formatPeriodLabel(isoString, period) {
  const d = new Date(isoString);
  if (period === "monthly") {
    return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function alertTypeLabel(alertType) {
  return alertType
    .toLowerCase()
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

function Reports() {
  const [period, setPeriod] = useState("weekly");
  const buckets = period === "weekly" ? 8 : 6;

  const { data, alertTypes, isLoading, error } = useHistoricalKpis({
    period,
    buckets,
  });

  const chartData = data.map((bucket) => ({
    label: formatPeriodLabel(bucket.period_start, period),
    customers_served: bucket.customers_served,
    avg_stay_minutes: bucket.avg_stay_minutes,
    ...bucket.alerts_by_type,
  }));

  const hasAnyData = data.some(
    (b) =>
      b.customers_served > 0 ||
      b.avg_stay_minutes > 0 ||
      b.total_worker_idle_seconds > 0 ||
      Object.keys(b.alerts_by_type ?? {}).length > 0,
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Reports</h1>
          <p className="text-muted-foreground text-sm">
            Trends across customers served, stay time, and alerts.
          </p>
        </div>

        <Tabs value={period} onValueChange={setPeriod}>
          <TabsList className="bg-muted">
            <TabsTrigger
              value="weekly"
              className="data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm"
            >
              Weekly
            </TabsTrigger>
            <TabsTrigger
              value="monthly"
              className="data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm"
            >
              Monthly
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <SectionCard title="Trends">
        {isLoading ? (
          <Skeleton className="h-[320px] w-full" />
        ) : !hasAnyData ? (
          <EmptyState
            icon={BarChart2}
            title="No data yet"
            description="Once customers, alerts, or worker activity are recorded, trends will appear here."
          />
        ) : (
          <div className="h-[320px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    background: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar
                  dataKey="customers_served"
                  name="Customers served"
                  fill="#3b82f6"
                  radius={[4, 4, 0, 0]}
                />
                {alertTypes.map((alertType, i) => (
                  <Bar
                    key={alertType}
                    dataKey={alertType}
                    name={alertTypeLabel(alertType)}
                    stackId="alerts"
                    fill={colorFor(alertType, i)}
                    radius={
                      i === alertTypes.length - 1 ? [4, 4, 0, 0] : undefined
                    }
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Details"
        count={isLoading || !hasAnyData ? undefined : data.length}
      >
        {isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : !hasAnyData ? (
          <EmptyState
            icon={BarChart2}
            title="No data yet"
            description="Historical data will appear here once activity is recorded."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-border border-b text-left text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">Period</th>
                  <th className="py-2 pr-4 font-medium">Customers served</th>
                  <th className="py-2 pr-4 font-medium">Avg stay (min)</th>
                  <th className="py-2 pr-4 font-medium">Worker idle (sec)</th>
                  {alertTypes.map((alertType) => (
                    <th key={alertType} className="py-2 pr-4 font-medium">
                      {alertTypeLabel(alertType)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.map((bucket) => (
                  <tr
                    key={bucket.period_start}
                    className="border-border border-b last:border-0"
                  >
                    <td className="py-2 pr-4">
                      {formatPeriodLabel(bucket.period_start, period)}
                    </td>
                    <td className="py-2 pr-4 tabular-nums">
                      {bucket.customers_served}
                    </td>
                    <td className="py-2 pr-4 tabular-nums">
                      {bucket.avg_stay_minutes}
                    </td>
                    <td className="py-2 pr-4 tabular-nums">
                      {bucket.total_worker_idle_seconds}
                    </td>
                    {alertTypes.map((alertType) => (
                      <td key={alertType} className="py-2 pr-4 tabular-nums">
                        {bucket.alerts_by_type?.[alertType] ?? 0}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

export default Reports;
