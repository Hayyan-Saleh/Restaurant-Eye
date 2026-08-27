import { useState, useEffect, useRef, useCallback } from "react";
import {
  getTablesStatus,
  getWorkersStatus,
  getZonesOccupancy,
} from "@/features/dashboard/liveStatusApi";
import { getRealtimeKpis } from "@/features/reports/kpisapi";

function getWsUrl() {
  const apiUrl = new URL(import.meta.env.VITE_API_URL);
  const wsProtocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${apiUrl.host}/ws/dashboard`;
}

export function useLiveStatus() {
  const [tables, setTables] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [zones, setZones] = useState([]);
  const [customersCurrent, setCustomersCurrent] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const socketRef = useRef(null);
  const retryTimeoutRef = useRef(null);
  const retryDelayRef = useRef(1000);
  const unmountedRef = useRef(false);
  const connectSocketRef = useRef(null);

  const loadInitialState = useCallback(async () => {
    setError(null);
    try {
      const [tablesRes, workersRes, zonesRes, kpisRes] = await Promise.all([
        getTablesStatus(),
        getWorkersStatus(),
        getZonesOccupancy(),
        getRealtimeKpis(),
      ]);
      if (unmountedRef.current) return;
      setTables(tablesRes.data.tables);
      setWorkers(workersRes.data.workers);
      setZones(zonesRes.data.zones);
      setCustomersCurrent(kpisRes.data.customers_current);
    } catch {
      if (!unmountedRef.current) {
        setError("Could not load live status.");
      }
    } finally {
      if (!unmountedRef.current) setIsLoading(false);
    }
  }, []);

  const handleEvent = useCallback((eventType, msg) => {
    switch (eventType) {
      case "TABLE_STATE_CHANGED": {
        setTables((prev) =>
          prev.map((t) =>
            t.zone_id === msg.zone_id
              ? { ...t, status: msg.details.new_state.toUpperCase() }
              : t,
          ),
        );
        break;
      }

      case "STAFF_IDLE": {
        setWorkers((prev) => {
          const exists = prev.some((w) => w.entity_id === msg.global_id);
          if (exists) {
            return prev.map((w) =>
              w.entity_id === msg.global_id ? { ...w, status: "IDLE" } : w,
            );
          }
          return [
            ...prev,
            {
              entity_id: msg.global_id,
              camera_id: msg.camera_id,
              zone_id: msg.zone_id,
              status: "IDLE",
            },
          ];
        });
        break;
      }

      case "WORKER_ACTIVE": {
        setWorkers((prev) => {
          const exists = prev.some((w) => w.entity_id === msg.global_id);
          if (exists) {
            return prev.map((w) =>
              w.entity_id === msg.global_id ? { ...w, status: "ACTIVE" } : w,
            );
          }
          return [
            ...prev,
            {
              entity_id: msg.global_id,
              camera_id: msg.camera_id,
              zone_id: msg.zone_id,
              status: "ACTIVE",
            },
          ];
        });
        break;
      }

      case "ZONE_OCCUPANCY_CHANGE": {
        setZones((prev) =>
          prev.map((z) =>
            z.zone_id === msg.zone_id
              ? { ...z, count: msg.details.new_count }
              : z,
          ),
        );
        break;
      }

      case "CUSTOMER_SEATED": {
        setCustomersCurrent((prev) => prev + 1);
        break;
      }

      case "CUSTOMER_LEFT": {
        setCustomersCurrent((prev) => Math.max(0, prev - 1));
        break;
      }

      default:
        break;
    }
  }, []);

  const connectSocket = useCallback(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      window.location.href = "/login";
      return;
    }

    const socket = new WebSocket(`${getWsUrl()}?token=${token}`);
    socketRef.current = socket;

    socket.onopen = () => {
      retryDelayRef.current = 1000;
    };

    socket.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      handleEvent(data.event_type, data);
    };

    socket.onclose = (event) => {
      socketRef.current = null;
      if (unmountedRef.current) return;

      if (event.code === 1008) {
        localStorage.removeItem("token");
        window.location.href = "/login";
        return;
      }

      retryTimeoutRef.current = setTimeout(async () => {
        await loadInitialState(); // no missed-event replay -> re-sync via REST
        connectSocketRef.current();
      }, retryDelayRef.current);
      retryDelayRef.current = Math.min(retryDelayRef.current * 2, 15000);
    };
  }, [handleEvent, loadInitialState]);

  // Keep the ref pointed at the current connectSocket. This runs after
  // render (not during it), which is what refs require.
  useEffect(() => {
    connectSocketRef.current = connectSocket;
  }, [connectSocket]);

  useEffect(() => {
    unmountedRef.current = false;

    (async () => {
      await loadInitialState();
      if (!unmountedRef.current) connectSocket();
    })();

    return () => {
      unmountedRef.current = true;
      clearTimeout(retryTimeoutRef.current);
      socketRef.current?.close(1000, "component unmounted");
    };
  }, [loadInitialState, connectSocket]);

  const tablesOccupied = tables.filter((t) => t.status === "OCCUPIED").length;
  const tablesDirty = tables.filter((t) => t.status === "DIRTY").length;
  const workersActive = workers.filter((w) => w.status === "ACTIVE").length;
  const workersIdle = workers.filter((w) => w.status === "IDLE").length;

  return {
    tables,
    workers,
    zones,
    isLoading,
    error,
    kpis: {
      tablesOccupied,
      tablesDirty,
      customersCurrent,
      workersCurrent: workers.length,
      workersActive,
      workersIdle,
    },
  };
}
