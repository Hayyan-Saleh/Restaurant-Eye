import { Users } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import EmptyState from "@/features/dashboard/components/EmptyState";
import SectionCard from "@/features/dashboard/components/SectionCard";
import WorkerItem from "./WorkerItem";

function WorkersSection({ workers = [], isLoading = false }) {
  return (
    <SectionCard title="Workers" count={isLoading ? undefined : workers.length}>
      {isLoading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-10 w-full" />
          ))}
        </div>
      ) : workers.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No workers detected"
          description="Workers appear here once they are recognised in a monitored zone."
        />
      ) : (
        <div className="divide-border flex flex-col divide-y">
          {workers.map((worker) => (
            <WorkerItem key={worker.entity_id} worker={worker} />
          ))}
        </div>
      )}
    </SectionCard>
  );
}

export default WorkersSection;
