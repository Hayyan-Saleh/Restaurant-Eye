import { BellOff } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import EmptyState from "@/features/dashboard/components/EmptyState";
import AlertItem from "./AlertItem";

function AlertsPanel({
  alerts = [],
  isOpen = false,
  onOpenChange,
  onResolve,
  isLoading = false,
  statusFilter = "ACTIVE",
  onFilterChange,
  resolvingIds = new Set(),
  error = "",
}) {
  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-0">
        <SheetHeader className="border-b">
          <SheetTitle>Alerts</SheetTitle>
          <SheetDescription>
            {isLoading
              ? "Loading alerts…"
              : `${alerts.length} alert${alerts.length === 1 ? "" : "s"} in view`}
          </SheetDescription>
          <div className="flex gap-2 pt-2">
            <Button
              size="sm"
              variant={statusFilter === "ACTIVE" ? "default" : "outline"}
              onClick={() => onFilterChange("ACTIVE")}
            >
              Active
            </Button>
            <Button
              size="sm"
              variant={statusFilter === "RESOLVED" ? "default" : "outline"}
              onClick={() => onFilterChange("RESOLVED")}
            >
              Resolved
            </Button>
          </div>
        </SheetHeader>
        {error && <p className="text-destructive px-4 pt-3 text-sm">{error}</p>}

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
          {isLoading ? (
            Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-[104px] w-full" />
            ))
          ) : alerts.length === 0 ? (
            <EmptyState
              icon={BellOff}
              title="All clear"
              description="No alerts to review right now."
            />
          ) : (
            alerts.map((alert) => (
              <AlertItem
                key={alert.id}
                alert={alert}
                onResolve={onResolve}
                isResolving={resolvingIds.has(alert.id)}
              />
            ))
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export default AlertsPanel;
