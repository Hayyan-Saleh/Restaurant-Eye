import { useEffect, useRef, useState } from "react";

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;
const TOKEN_KEY = "token";

export default function useDashboardSocket({ onEvent } = {}) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const retryMsRef = useRef(RECONNECT_MIN_MS);
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    let disposed = false;
    let retryTimer = null;

    const connect = () => {
      const token = localStorage.getItem(TOKEN_KEY);
      if (!token) {
        setConnected(false);
        return;
      }
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${protocol}://${window.location.host}/ws/dashboard?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        retryMsRef.current = RECONNECT_MIN_MS;
        setConnected(true);
      };

      ws.onmessage = (e) => {
        try {
          onEventRef.current?.(JSON.parse(e.data));
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onclose = (e) => {
        setConnected(false);
        if (disposed) return;
        if (e.code === 1008) {
          localStorage.removeItem(TOKEN_KEY);
          window.location.href = "/login";
          return;
        }
        retryTimer = setTimeout(connect, retryMsRef.current);
        retryMsRef.current = Math.min(retryMsRef.current * 2, RECONNECT_MAX_MS);
      };

      ws.onerror = () => ws.close();
    };

    connect();

    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, []);

  return { connected };
}
