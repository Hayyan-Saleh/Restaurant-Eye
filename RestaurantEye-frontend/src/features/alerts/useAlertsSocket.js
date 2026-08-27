import { useEffect, useRef, useCallback } from "react";
import { toast } from "@/hooks/use-toast";

// Same WS URL derivation as useLiveStatus — kept local here since this
// hook has no other dependency on the liveStatus feature.
function getWsUrl() {
  const apiUrl = new URL(import.meta.env.VITE_API_URL);
  const wsProtocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${apiUrl.host}/ws/dashboard`;
}

function alertTitle(alertType) {
  if (alertType === "WORKER_IDLE_TOO_LONG") return "Worker idle too long";
  if (alertType === "DELAY_ALERT") return "Table service delayed";
  return "New alert";
}

/**
 * Listens on the same /ws/dashboard channel as useLiveStatus, but only
 * reacts to NEW_ALERT events — fires a toast immediately, and hands the
 * raw event to an optional onNewAlert callback (e.g. to bump a badge
 * count or trigger a GET /alerts/ refetch while the panel is open).
 *
 * Deliberately separate from useLiveStatus: alerts is its own feature
 * (its own sidebar panel, badge, API file) and shouldn't have to load
 * tables/workers/zones state it doesn't use just to hear about alerts.
 *
 * NOTE: this opens its own WebSocket connection, independent of the one
 * useLiveStatus opens on the dashboard page. Both listen to the same
 * server-side channel, so this is safe, but if the alerts sidebar and
 * the dashboard are ever mounted at the same time, that's two open
 * sockets to the same endpoint. Fine for now; revisit if it becomes a
 * problem (e.g. lift into a shared context/provider higher in the tree).
 */
export function useAlertsSocket({ onNewAlert, enabled = true } = {}) {
  const socketRef = useRef(null);
  const retryTimeoutRef = useRef(null);
  const retryDelayRef = useRef(1000);
  const unmountedRef = useRef(false);
  const connectRef = useRef(null);
  // Always points at the latest onNewAlert. onmessage below reads through
  // this ref instead of closing over the prop directly — otherwise a
  // NEW_ALERT arriving between renders (e.g. after the caller's filter
  // state changes) would run whatever version of onNewAlert existed when
  // the socket last connected, not the current one.
  const onNewAlertRef = useRef(onNewAlert);
  useEffect(() => {
    onNewAlertRef.current = onNewAlert;
  }, [onNewAlert]);

  const connect = useCallback(() => {
    const token = localStorage.getItem("token");
    if (!token) return;

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

      if (data.event_type !== "NEW_ALERT") return;

      const { alert_type, message } = data.details ?? {};

      toast({
        title: alertTitle(alert_type),
        description: message,
        variant: "destructive",
      });

      onNewAlertRef.current?.(data);
    };

    socket.onclose = (event) => {
      socketRef.current = null;
      if (unmountedRef.current) return;

      if (event.code === 1008) {
        localStorage.removeItem("token");
        window.location.href = "/login";
        return;
      }

      retryTimeoutRef.current = setTimeout(() => {
        connectRef.current();
      }, retryDelayRef.current);
      retryDelayRef.current = Math.min(retryDelayRef.current * 2, 15000);
    };
  }, []);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    if (!enabled) return;
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      clearTimeout(retryTimeoutRef.current);
      socketRef.current?.close(1000, "component unmounted");
    };
  }, [enabled, connect]);
}
