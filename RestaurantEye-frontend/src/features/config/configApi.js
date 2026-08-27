import api from "@/lib/axios";

export const createZone = (zone) => api.post("/zones/", zone);
export const updateZone = (zoneId, zone) => api.put(`/zones/${zoneId}`, zone);
export const deleteZoneApi = (zoneId) => api.delete(`/zones/${zoneId}`);
export const getZonesForCamera = (cameraId) =>
  api.get(`/zones/cameras/${cameraId}`);
export const getCameraSnapshot = (cameraId) =>
  api.get(`/zones/cameras/${cameraId}/snapshot`, { responseType: "blob" });
