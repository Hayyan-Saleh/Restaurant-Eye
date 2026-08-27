import api from "@/lib/axios";

export const getTablesStatus = () => api.get("/live-status/tables/status");

export const getWorkersStatus = () => api.get("/live-status/workers/status");

export const getZonesOccupancy = () => api.get("/live-status/zones-occupancy");

export const getRealtimeKpis = () => api.get("/kpis/realtime");
