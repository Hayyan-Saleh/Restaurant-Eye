import { useState, useEffect, useCallback } from "react";
import { getHistoricalKpis } from "@/features/reports/kpisapi";

function dateKey(d) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function startOfPeriod(date, period) {
  const d = new Date(date);
  if (period === "monthly") {
    d.setDate(1);
  } else {
    const day = d.getDay();
    const diff = (day === 0 ? -6 : 1) - day;
    d.setDate(d.getDate() + diff);
  }
  d.setHours(0, 0, 0, 0);
  return d;
}

function fillGaps(apiBuckets, period, bucketCount) {
  const byPeriodStart = new Map(
    apiBuckets.map((b) => [b.period_start.slice(0, 10), b]),
  );

  const now = startOfPeriod(new Date(), period);

  const points = [now];
  for (let i = 1; i < bucketCount; i++) {
    const prev = new Date(points[0]);
    if (period === "monthly") {
      prev.setMonth(prev.getMonth() - 1);
    } else {
      prev.setDate(prev.getDate() - 7);
    }
    points.unshift(prev);
  }

  return points.map((p) => {
    const key = dateKey(p);
    return (
      byPeriodStart.get(key) ?? {
        period_start: key + "T00:00:00",
        customers_served: 0,
        avg_stay_minutes: 0,
        total_worker_idle_seconds: 0,
        alerts_by_type: {},
      }
    );
  });
}

export function useHistoricalKpis({ period = "weekly", buckets = 8 } = {}) {
  const [data, setData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await getHistoricalKpis(period, buckets);
      setData(fillGaps(res.data.buckets, period, buckets));
    } catch {
      setError("Could not load historical KPIs.");
    } finally {
      setIsLoading(false);
    }
  }, [period, buckets]);

  useEffect(() => {
    load();
  }, [load]);

  const alertTypes = Array.from(
    new Set(data.flatMap((b) => Object.keys(b.alerts_by_type ?? {}))),
  );

  return { data, alertTypes, isLoading, error, reload: load };
}
