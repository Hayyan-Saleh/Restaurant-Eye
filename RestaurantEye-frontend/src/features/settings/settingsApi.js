import api from "@/lib/axios";

export const getSettings = () => api.get("/settings/");
export const getSettingsDetail = () => api.get("/settings/detail");
export const updateSettings = (body) => api.put("/settings/", body);
export const resetSettings = () => api.post("/settings/reset");
