import { memo, useState, useEffect, useCallback } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import { useTheme } from "@/hooks/useTheme";
import { useAlertsSocket } from "@/features/alerts/useAlertsSocket";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import {
  LayoutDashboard,
  Cctv,
  BarChart2,
  Moon,
  Sun,
  User,
  Settings,
  SlidersHorizontal,
  LogOut,
} from "lucide-react";
import { logout } from "@/features/auth/authApi";
import AlertsBadge from "@/features/alerts/components/AlertsBadge";
import AlertsPanel from "@/features/alerts/components/AlertsPanel";
import { getAlerts, resolveAlert } from "@/features/alerts/alertsApi";

const menuItems = [
  { title: "Dashboard", icon: LayoutDashboard, to: "/dashboard" },
  { title: "Live Feed", icon: Cctv, to: "/live" },
  { title: "Reports", icon: BarChart2, to: "/reports" },
  { title: "Config", icon: Settings, to: "/config" },
  { title: "Settings", icon: SlidersHorizontal, to: "/settings" },
];

export const AdminSidebar = memo(() => {
  const { theme, setTheme } = useTheme();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const [isAlertsPanelOpen, setIsAlertsPanelOpen] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [isAlertsLoading, setIsAlertsLoading] = useState(true);
  const [alertsFilter, setAlertsFilter] = useState("ACTIVE");
  const [resolvingIds, setResolvingIds] = useState(new Set());
  const [alertsError, setAlertsError] = useState("");

  const loadAlerts = useCallback(async () => {
    setAlertsError("");
    try {
      const res = await getAlerts(alertsFilter);
      setAlerts(res.data.alerts);
    } catch {
      setAlertsError("Couldn't load alerts. Try reopening the panel.");
    } finally {
      setIsAlertsLoading(false);
    }
  }, [alertsFilter]);

  useEffect(() => {
    loadAlerts();
  }, [loadAlerts]);

  useAlertsSocket({
    onNewAlert: () => {
      // Only refetch if they're viewing ACTIVE — a NEW_ALERT is always
      // active by definition, no point refetching the RESOLVED tab.
      if (alertsFilter === "ACTIVE") loadAlerts();
    },
  });

  const handleResolve = async (id) => {
    setResolvingIds((prev) => new Set(prev).add(id));
    setAlertsError("");
    try {
      await resolveAlert(id);
      setAlerts((current) => current.filter((alert) => alert.id !== id));
    } catch {
      setAlertsError(
        "Couldn't resolve that alert. It may already be resolved.",
      );
    } finally {
      setResolvingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const activeAlertsCount = alertsFilter === "ACTIVE" ? alerts.length : 0;

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // ignore — proceed to clear local session regardless
    } finally {
      localStorage.removeItem("token");
      navigate("/login");
    }
  };

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <NavLink to="/dashboard">
                <div className="bg-primary text-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg">
                  <LayoutDashboard className="h-5 w-5" />
                </div>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-semibold">Restaurant Eye</span>
                  <span className="truncate text-xs">Admin Panel</span>
                </div>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {menuItems.map((item) => {
                const Icon = item.icon;
                return (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      isActive={pathname === item.to}
                      tooltip={item.title}
                    >
                      <NavLink to={item.to}>
                        <Icon />
                        <span>{item.title}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
              <AlertsBadge
                count={activeAlertsCount}
                onClick={() => setIsAlertsPanelOpen(true)}
              />
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <AlertsPanel
        alerts={alerts}
        isOpen={isAlertsPanelOpen}
        onOpenChange={setIsAlertsPanelOpen}
        onResolve={handleResolve}
        isLoading={isAlertsLoading}
        statusFilter={alertsFilter}
        onFilterChange={setAlertsFilter}
        resolvingIds={resolvingIds}
        error={alertsError}
      />

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun /> : <Moon />}
              <span>{theme === "dark" ? "Light Mode" : "Dark Mode"}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <NavLink to="/profile">
                <User />
                <span>Admin Profile</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={handleLogout}>
              <LogOut />
              <span>Logout</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
});
AdminSidebar.displayName = "AdminSidebar";
