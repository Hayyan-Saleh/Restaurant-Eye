function ZoneOccupancyCard({ zone }) {
  return (
    <div className="border-border flex items-center justify-between gap-3 rounded-md border p-3">
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm font-medium">
          {zone.zone_name ?? zone.zone_id}
        </span>
        <span className="text-muted-foreground truncate text-xs">
          {zone.camera_id}
        </span>
      </div>
      <span className="text-xl font-semibold tabular-nums">{zone.count}</span>
    </div>
  );
}

export default ZoneOccupancyCard;
