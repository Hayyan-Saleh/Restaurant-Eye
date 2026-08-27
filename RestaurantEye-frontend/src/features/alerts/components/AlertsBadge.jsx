import { BellRing } from "lucide-react";
import {
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";

function AlertsBadge({ count = 0, onClick }) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton onClick={onClick} tooltip="Alerts">
        <BellRing />
        <span>Alerts</span>
      </SidebarMenuButton>
      {count > 0 && <SidebarMenuBadge>{count}</SidebarMenuBadge>}
    </SidebarMenuItem>
  );
}

export default AlertsBadge;
