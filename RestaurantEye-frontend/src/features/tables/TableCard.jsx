import { Badge } from "@/components/ui/badge";

const STATUS_VARIANTS = {
  FREE: "secondary",
  OCCUPIED: "default",
  DIRTY: "destructive",
};

function TableCard({ table }) {
  return (
    <div className="border-border flex flex-col gap-2 rounded-md border p-3">
      <div className="flex items-start justify-between gap-2">
        <span className="truncate font-mono text-sm font-medium">
          {table.zone_id}
        </span>
        <Badge variant={STATUS_VARIANTS[table.status] ?? "outline"}>
          {table.status}
        </Badge>
      </div>
      <span className="text-muted-foreground truncate text-xs">
        {table.camera_id ?? "No camera"}
      </span>
    </div>
  );
}

export default TableCard;
