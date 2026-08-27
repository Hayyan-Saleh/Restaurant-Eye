import api from "@/lib/axios";

export const login = (email, password) =>
  api.post("/auth/login", { email, password });

export const getMe = () => api.get("/auth/me");

export const logout = () => api.post("/auth/logout");

export const requestOtp = (email) =>
  api.post("/auth/password/request-otp", { email });

export const resetPassword = (email, otp, newPassword) =>
  api.post("/auth/password/reset", {
    email,
    otp,
    new_password: newPassword,
  });
