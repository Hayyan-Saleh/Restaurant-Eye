import { useState, useRef, useCallback, useEffect } from "react";
import { CAMERAS } from "./cameras";
import { ZONE_TYPES } from "./zoneTypes";
import {
  getZonesForCamera,
  getCameraSnapshot,
  createZone,
  updateZone,
  deleteZoneApi,
} from "./configApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ZONE_COLORS = {
  service_path: "#f97316",
  table_area: "#22c55e",
  table: "#3b82f6",
};

const getDisplayType = (z) => z.original_zone_type ?? z.zone_type;

function ZoneAnnotator() {
  const [currentCam, setCurrentCam] = useState(null);
  const [images, setImages] = useState({});
  const [zonesByCam, setZonesByCam] = useState({});
  const [points, setPoints] = useState([]);
  const [zoneType, setZoneType] = useState("table_area");
  const [customType, setCustomType] = useState("");
  const [zoneName, setZoneName] = useState("");
  const [scale, setScale] = useState(1);
  const [status, setStatus] = useState("");
  const [editingZoneId, setEditingZoneId] = useState(null);
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);

  const activeType = zoneType === "custom" ? customType : zoneType;

  const setupCanvas = (img) => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const mw = wrap.clientWidth - 4;
    const s = mw / img.naturalWidth;
    setScale(s);
    const canvas = canvasRef.current;
    canvas.width = Math.round(img.naturalWidth * s);
    canvas.height = Math.round(img.naturalHeight * s);
  };

  const drawPolygon = (ctx, pts, label, color = "#22c55e") => {
    ctx.strokeStyle = color;
    ctx.fillStyle = color + "1f";
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    if (label && pts[0]) {
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "11px sans-serif";
      ctx.fillText(label, pts[0][0] + 4, pts[0][1] - 4);
    }
  };

  const draw = useCallback(
    (img, s, pts) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (img) ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

      const zones = zonesByCam[currentCam] || [];
      zones.forEach((z) =>
        drawPolygon(
          ctx,
          z.polygon_coordinates.points.map((p) => [p.x * s, p.y * s]),
          z.name,
          ZONE_COLORS[getDisplayType(z)] || "#22c55e",
        ),
      );

      if (pts.length) {
        ctx.strokeStyle = "#3b82f6";
        ctx.fillStyle = "rgba(59,130,246,0.15)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        pts.forEach(([x, y], i) =>
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y),
        );
        ctx.stroke();
        pts.forEach(([x, y]) => {
          ctx.beginPath();
          ctx.arc(x, y, 4, 0, Math.PI * 2);
          ctx.fillStyle = "#3b82f6";
          ctx.fill();
        });
      }
    },
    [zonesByCam, currentCam],
  );

  const selectCamera = async (camId) => {
    setCurrentCam(camId);
    setPoints([]);
    setStatus("");

    if (!zonesByCam[camId]) {
      try {
        const res = await getZonesForCamera(camId);
        setZonesByCam((prev) => ({ ...prev, [camId]: res.data }));
      } catch {
        setStatus("Failed to load zones for this camera");
      }
    }

    if (images[camId]) {
      requestAnimationFrame(() => setupCanvas(images[camId].img));
      return;
    }

    try {
      const res = await getCameraSnapshot(camId);
      const url = URL.createObjectURL(res.data);
      const img = new Image();
      img.onload = () => {
        setImages((prev) => ({
          ...prev,
          [camId]: { img, w: img.naturalWidth, h: img.naturalHeight },
        }));
        requestAnimationFrame(() => setupCanvas(img));
        URL.revokeObjectURL(url);
      };
      img.onerror = () => setStatus("No reference image found for this camera");
      img.src = url;
    } catch {
      setStatus("No reference image found for this camera");
    }
  };

  const handleCanvasClick = (e) => {
    if (!currentCam || !images[currentCam]) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const newPts = [...points, [x, y]];
    setPoints(newPts);
    draw(images[currentCam].img, scale, newPts);
  };

  const undoPoint = () => {
    const newPts = points.slice(0, -1);
    setPoints(newPts);
    draw(images[currentCam]?.img, scale, newPts);
  };

  const finishZone = async () => {
    if (points.length < 3) {
      setStatus("Need at least 3 points");
      return;
    }
    if (!activeType) {
      setStatus("Set a zone type");
      return;
    }
    const origPoints = points.map(([x, y]) => ({
      x: Math.round(x / scale),
      y: Math.round(y / scale),
    }));
    const existing = zonesByCam[currentCam] || [];
    const imgData = images[currentCam];
    const newZone = {
      id: null,
      camera_id: currentCam,
      name: zoneName || `${activeType} ${existing.length + 1}`,
      zone_type: activeType,
      original_zone_type: activeType,
      polygon_coordinates: {
        points: origPoints,
        image_size: { width: imgData.w, height: imgData.h },
      },
      parent_zone_id: null,
      excludes_tables: false,
      auto_generated: false,
    };
    try {
      let saved;
      if (editingZoneId) {
        const res = await updateZone(editingZoneId, newZone);
        saved = res.data;
        const updated = {
          ...zonesByCam,
          [currentCam]: existing.map((z) =>
            z.id === editingZoneId ? saved : z,
          ),
        };
        setZonesByCam(updated);
      } else {
        const res = await createZone(newZone);
        saved = res.data;
        setZonesByCam({ ...zonesByCam, [currentCam]: [...existing, saved] });
      }
      setPoints([]);
      setZoneName("");
      setEditingZoneId(null);
      setStatus(`Saved "${saved.name}"`);
      draw(images[currentCam].img, scale, []);
    } catch {
      setStatus("Failed to save zone");
    }
  };

  const deleteZone = async (index) => {
    const zone = zonesByCam[currentCam][index];
    try {
      await deleteZoneApi(zone.id);
      const updated = {
        ...zonesByCam,
        [currentCam]: zonesByCam[currentCam].filter((_, i) => i !== index),
      };
      setZonesByCam(updated);
      draw(images[currentCam]?.img, scale, points);
    } catch {
      setStatus("Failed to delete zone");
    }
  };

  const buildOutput = () => {
    const out = {};
    for (const cam of CAMERAS) {
      const imgData = images[cam.id];
      out[cam.id] = {
        image_size: imgData
          ? { width: imgData.w, height: imgData.h }
          : { width: 1920, height: 1080 },
        zones: zonesByCam[cam.id] || [],
      };
    }
    return out;
  };

  const handleExport = () => {
    const config = buildOutput();
    const blob = new Blob([JSON.stringify(config, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "zones_config.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const currentZones = zonesByCam[currentCam] || [];

  useEffect(() => {
    if (currentCam && images[currentCam]) {
      draw(images[currentCam].img, scale, points);
    }
  }, [currentCam, images, zonesByCam, scale, points, draw]);
  return (
    <div className="flex gap-4 h-full min-h-0">
      <Card className="w-64 flex flex-col overflow-hidden">
        <CardHeader>
          <CardTitle className="text-sm">Cameras</CardTitle>
        </CardHeader>
        <CardContent className="flex-1 min-h-0 overflow-hidden flex flex-col gap-1">
          {CAMERAS.map((cam) => {
            const zs = zonesByCam[cam.id] || [];
            return (
              <button
                key={cam.id}
                onClick={() => selectCamera(cam.id)}
                className={`text-left px-3 py-2 rounded-md text-sm ${
                  currentCam === cam.id ? "bg-accent" : "hover:bg-accent/50"
                }`}
              >
                <div className="font-medium">{cam.name}</div>
                <div className="text-xs text-muted-foreground">
                  {zs.length} zone{zs.length !== 1 ? "s" : ""}
                  {images[cam.id] ? "" : " · no image"}
                </div>
              </button>
            );
          })}
        </CardContent>
      </Card>

      <div className="flex-1 flex flex-col gap-3 min-h-0">
        <Card>
          <CardContent className="flex items-center gap-3 py-3">
            <span className="text-sm font-medium">
              {currentCam
                ? CAMERAS.find((c) => c.id === currentCam)?.name
                : "Select a camera"}
            </span>
            <div className="flex-1" />
            <Select value={zoneType} onValueChange={setZoneType}>
              <SelectTrigger className="w-44">
                <SelectValue placeholder="Zone type" />
              </SelectTrigger>
              <SelectContent>
                {ZONE_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {zoneType === "custom" && (
              <Input
                placeholder="custom type"
                value={customType}
                onChange={(e) => setCustomType(e.target.value)}
                className="w-36"
              />
            )}
            <Input
              placeholder="Zone name (optional)"
              value={zoneName}
              onChange={(e) => setZoneName(e.target.value)}
              className="w-44"
            />
            <Button variant="outline" onClick={undoPoint}>
              Undo
            </Button>
            <Button onClick={finishZone}>Save Zone</Button>
          </CardContent>
        </Card>

        <div
          ref={wrapRef}
          className="flex-1 min-h-0 bg-black/50 rounded-md overflow-auto flex items-start justify-center p-2"
        >
          <canvas
            ref={canvasRef}
            onClick={handleCanvasClick}
            className="cursor-crosshair rounded-sm shadow-lg"
          />
        </div>

        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground flex-1">{status}</span>
          <Button variant="outline" onClick={handleExport}>
            Export JSON
          </Button>
        </div>
      </div>

      <Card className="w-56 flex flex-col overflow-hidden">
        <CardHeader>
          <CardTitle className="text-sm">Zones</CardTitle>
        </CardHeader>
        <CardContent className="flex-1 min-h-0 overflow-hidden flex flex-col gap-1">
          {currentZones.length === 0 && (
            <p className="text-xs text-muted-foreground">No zones yet</p>
          )}
          {currentZones.map((z, i) => (
            <div
              key={z.id || i}
              className="flex items-center justify-between text-xs py-1"
            >
              <span
                className="truncate cursor-pointer"
                onClick={() => {
                  setEditingZoneId(z.id);
                  setZoneName(z.name);
                  setZoneType(getDisplayType(z));
                }}
              >
                {z.name}
              </span>
              <button
                onClick={() => deleteZone(i)}
                className="text-destructive px-1"
              >
                ×
              </button>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

export default ZoneAnnotator;
