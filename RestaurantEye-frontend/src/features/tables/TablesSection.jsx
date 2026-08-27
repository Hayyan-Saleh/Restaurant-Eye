import { Utensils } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import EmptyState from "@/features/dashboard/components/EmptyState";
import SectionCard from "@/features/dashboard/components/SectionCard";
import TableCard from "./TableCard";

const GRID = "grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4";

function TablesSection({ tables = [], isLoading = false }) {
  return (
    <SectionCard title="Tables" count={isLoading ? undefined : tables.length}>
      {isLoading ? (
        <div className={GRID}>
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="h-[74px] w-full" />
          ))}
        </div>
      ) : tables.length === 0 ? (
        <EmptyState
          icon={Utensils}
          title="No tables tracked"
          description="Configure table zones for a camera to start tracking their status."
        />
      ) : (
        <div className={GRID}>
          {tables.map((table) => (
            <TableCard key={table.zone_id} table={table} />
          ))}
        </div>
      )}
    </SectionCard>
  );
}

export default TablesSection;
