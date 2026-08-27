import api from "@/lib/axios";

export const getAlerts = (status) =>
  api.get("/alerts/", { params: status ? { status } : {} });

export const resolveAlert = (alertId) => api.post(`/alerts/${alertId}/resolve`);
