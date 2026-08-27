import { MapPin } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import EmptyState from "@/features/dashboard/components/EmptyState";
import SectionCard from "@/features/dashboard/components/SectionCard";
import ZoneOccupancyCard from "./ZoneOccupancyCard";

const GRID = "grid gap-3 sm:grid-cols-2 lg:grid-cols-4";

function ZonesOccupancySection({ zones = [], isLoading = false }) {
  return (
    <SectionCard
      title="Zone Occupancy"
      count={isLoading ? undefined : zones.length}
    >
      {isLoading ? (
        <div className={GRID}>
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-[66px] w-full" />
          ))}
        </div>
      ) : zones.length === 0 ? (
        <EmptyState
          icon={MapPin}
          title="No zones configured"
          description="Add zones in the camera configuration to see live occupancy counts."
        />
      ) : (
        <div className={GRID}>
          {zones.map((zone) => (
            <ZoneOccupancyCard
              key={`${zone.camera_id}-${zone.zone_id}`}
              zone={zone}
            />
          ))}
        </div>
      )}
    </SectionCard>
  );
}

export default ZonesOccupancySection;
