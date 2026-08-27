import { Badge } from "@/components/ui/badge";

const STATUS_VARIANTS = {
  ACTIVE: "default",
  IDLE: "secondary",
};

function shortenEntityId(entityId) {
  const tail = entityId.split("_").pop() ?? entityId;
  return tail.length > 8 ? `${tail.slice(0, 8)}…` : tail;
}

function WorkerItem({ worker }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-mono text-sm font-medium">
          {shortenEntityId(worker.entity_id)}
        </span>
        <span className="text-muted-foreground truncate text-xs">
          {worker.zone_id ?? "Unassigned"}
        </span>
      </div>
      <Badge variant={STATUS_VARIANTS[worker.status] ?? "outline"}>
        {worker.status}
      </Badge>
    </div>
  );
}

export default WorkerItem;
