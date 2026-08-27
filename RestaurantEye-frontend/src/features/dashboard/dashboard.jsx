import { useLiveStatus } from "@/hooks/useLiveStatus";
import TablesSection from "@/features/tables/TablesSection";
import WorkersSection from "@/features/workers/WorkersSection";
import ZonesOccupancySection from "@/features/zones/ZonesOccupancySection";
import { Skeleton } from "@/components/ui/skeleton";

function KpiStat({ label, value, isLoading }) {
  return (
    <div className="border-border flex flex-col gap-1 rounded-lg border p-3">
      <span className="text-muted-foreground text-xs">{label}</span>
      {isLoading ? (
        <Skeleton className="h-7 w-12" />
      ) : (
        <span className="text-2xl font-semibold tabular-nums">{value}</span>
      )}
    </div>
  );
}

function Dashboard() {
  const { tables, workers, zones, kpis, isLoading, error } = useLiveStatus();

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-muted-foreground text-sm">
          Live status across tables, workers and zones.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <KpiStat
          label="Tables occupied"
          value={kpis.tablesOccupied}
          isLoading={isLoading}
        />
        <KpiStat
          label="Tables dirty"
          value={kpis.tablesDirty}
          isLoading={isLoading}
        />
        <KpiStat
          label="Customers"
          value={kpis.customersCurrent}
          isLoading={isLoading}
        />
        <KpiStat
          label="Workers"
          value={kpis.workersCurrent}
          isLoading={isLoading}
        />
        <KpiStat
          label="Workers active"
          value={kpis.workersActive}
          isLoading={isLoading}
        />
        <KpiStat
          label="Workers idle"
          value={kpis.workersIdle}
          isLoading={isLoading}
        />
      </div>

      <WorkersSection workers={workers} isLoading={isLoading} />

      <TablesSection tables={tables} isLoading={isLoading} />

      <ZonesOccupancySection zones={zones} isLoading={isLoading} />
    </div>
  );
}

export default Dashboard;
