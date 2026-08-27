import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "../relativeTime";

function formatAlertType(alertType) {
  return alertType.replaceAll("_", " ").toLowerCase();
}

function AlertItem({ alert, onResolve, isResolving = false }) {
  const isResolved = alert.status === "RESOLVED";

  return (
    <div className="border-border flex flex-col gap-2 rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <Badge variant={isResolved ? "secondary" : "destructive"}>
          {formatAlertType(alert.alert_type)}
        </Badge>
        <span className="text-muted-foreground shrink-0 text-xs">
          {formatRelativeTime(alert.created_at)}
        </span>
      </div>

      <p className="text-sm">{alert.message}</p>

      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground truncate text-xs">
          {[alert.camera_id, alert.zone_id].filter(Boolean).join(" · ") || "—"}
        </span>
        {!isResolved && (
          <Button
            variant="outline"
            size="sm"
            disabled={isResolving}
            onClick={() => onResolve(alert.id)}
          >
            {isResolving ? "Resolving…" : "Resolve"}
          </Button>
        )}
      </div>
    </div>
  );
}

export default AlertItem;
