import api from "@/lib/axios";

export const getRealtimeKpis = () => api.get("/kpis/realtime");

export const getHistoricalKpis = (period = "weekly", buckets = 8) =>
  api.get("/kpis/historical", { params: { period, buckets } });
