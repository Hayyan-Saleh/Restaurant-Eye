import { Routes, Route, Navigate } from "react-router-dom";
import Auth from "./features/auth/auth";
import ProtectedRoute from "./features/auth/ProtectedRoute";
import Dashboard from "@/features/dashboard/dashboard";
import Live from "@/features/live/live";
import Reports from "@/features/reports/reports";
import Profile from "@/features/profile/profile";
import AppLayout from "./layouts/AppLayout";
import Config from "@/features/config/config";
import Settings from "@/features/settings/settings";

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Auth />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<AppLayout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/config" element={<Config />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="/live" element={<Live />} />
      </Route>
    </Routes>
  );
}

export default App;
