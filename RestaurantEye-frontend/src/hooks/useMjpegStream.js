import { useCallback, useEffect, useRef, useState } from "react";

const TOKEN_KEY = "token";
const ERROR_RETRY_MS = 20_000;
const PROBE_INTERVAL_MS = 4_000;
const ANNOTATED_RECONNECT_MS = 30_000;


function getApiBase() {
  const base = import.meta.env.VITE_API_URL || "";
  if (!base) return "/api/v1";
  if (base.startsWith("http")) {
    try {
      const url = new URL(base);
      return url.pathname && url.pathname !== "/" ? url.pathname : base;
    } catch {
      return base;
    }
  }
  return base;
}

function buildStreamUrl(cameraId, token, cacheBuster) {
  const url = `${getApiBase()}/video/${cameraId}/stream?token=${encodeURIComponent(token)}`;
  return cacheBuster ? `${url}&_t=${cacheBuster}` : url;
}

export default function useMjpegStream(cameraId, { probe = false, reloadSignal = 0 } = {}) {
  const [status, setStatus] = useState(() =>
    localStorage.getItem(TOKEN_KEY) ? "loading" : "noToken",
  );
  const [mode, setMode] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);
  const timersRef = useRef([]);
  const probeControllerRef = useRef(null);
  const prevModeRef = useRef(null);

  const token = localStorage.getItem(TOKEN_KEY);
  const src = token ? buildStreamUrl(cameraId, token, reloadToken) : null;

  const clearTimers = useCallback(() => {
    timersRef.current.forEach((t) => clearTimeout(t));
    timersRef.current = [];
  }, []);

  const reload = useCallback(() => {
    setStatus(() => "loading");
    setReloadToken((t) => t + 1);
  }, []);

  const retry = useCallback(() => {
    const current = localStorage.getItem(TOKEN_KEY);
    if (!current) {
      setStatus(() => "noToken");
      return;
    }
    reload();
  }, [reload]);

  useEffect(() => {
    return () => {
      clearTimers();
      probeControllerRef.current?.abort();
    };
  }, [clearTimers]);

  const handleLoad = useCallback(() => setStatus("streaming"), []);
  const handleError = useCallback(() => setStatus("error"), []);

  useEffect(() => {
    if (status !== "error") return;
    timersRef.current.push(setTimeout(retry, ERROR_RETRY_MS));
  }, [status, retry]);

  const probeMode = useCallback(async () => {
    const current = localStorage.getItem(TOKEN_KEY);
    if (!current) return;
    const controller = new AbortController();
    probeControllerRef.current = controller;
    try {
      const res = await fetch(buildStreamUrl(cameraId, current), {
        signal: controller.signal,
      });
      const source = res.headers.get("X-Stream-Source");
      if (source === "annotated") setMode("annotated");
      else if (source === "raw") setMode("raw");
      else setMode("error");
      res.body?.cancel();
    } catch (err) {
      if (err.name !== "AbortError") setMode("error");
    }
  }, [cameraId]);

  useEffect(() => {
    if (!probe) return;
    const id = setInterval(probeMode, PROBE_INTERVAL_MS);
    const initial = setTimeout(probeMode, 0);
    return () => {
      clearInterval(id);
      clearTimeout(initial);
      probeControllerRef.current?.abort();
    };
  }, [probe, probeMode]);

  useEffect(() => {
    const prev = prevModeRef.current;
    if (mode === "annotated" && prev !== "annotated") reload();
    prevModeRef.current = mode;
  }, [mode, reload]);

  useEffect(() => {
    if (reloadSignal <= 0) return;
    const t = setTimeout(reload, 0);
    return () => clearTimeout(t);
  }, [reloadSignal, reload]);

  useEffect(() => {
    if (status !== "streaming") return;
    const id = setInterval(reload, ANNOTATED_RECONNECT_MS);
    return () => clearInterval(id);
  }, [status, reload]);

  return { src, status, mode, reloadToken, retry, handleLoad, handleError };
}
