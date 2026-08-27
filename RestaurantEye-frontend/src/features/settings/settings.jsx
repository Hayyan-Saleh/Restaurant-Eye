import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/hooks/use-toast";
import { getSettingsDetail, updateSettings, resetSettings } from "./settingsApi";
import { formatRelativeTime } from "@/features/alerts/relativeTime";

const RECOMMENDED_VALUES = [30, 60, 90, 120, 180, 300, 600];
const MIN_IDLE_LIMIT = 10;
const MAX_IDLE_LIMIT = 3600;

function Settings() {
  const [settings, setSettings] = useState(null);
  const [formValue, setFormValue] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [loadError, setLoadError] = useState(null);

  const refresh = async () => {
    const { data } = await getSettingsDetail();
    setSettings(data);
    setFormValue(String(data.worker_idle_limit));
    return data;
  };

  useEffect(() => {
    let cancelled = false;
    getSettingsDetail()
      .then(({ data }) => {
        if (cancelled) return;
        setSettings(data);
        setFormValue(String(data.worker_idle_limit));
      })
      .catch((error) => {
        if (cancelled) return;
        setLoadError(error);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSave = async () => {
    const value = Number(formValue);
    if (Number.isNaN(value) || value < MIN_IDLE_LIMIT || value > MAX_IDLE_LIMIT) {
      toast({
        title: "Invalid idle limit",
        description: `Must be between ${MIN_IDLE_LIMIT} and ${MAX_IDLE_LIMIT} seconds.`,
        type: "error",
      });
      return;
    }
    setIsSaving(true);
    try {
      await updateSettings({ worker_idle_limit: value });
      await refresh();
      toast({
        title: "Saved",
        description: "Idle limit updated.",
        type: "success",
      });
    } catch (error) {
      const message =
        error.response?.data?.detail?.[0]?.msg ??
        error.message ??
        "Failed to update settings.";
      toast({
        title: "Update failed",
        description: message,
        type: "error",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = async () => {
    setIsSaving(true);
    try {
      await resetSettings();
      await refresh();
      toast({
        title: "Reset",
        description: "Idle limit reset to defaults.",
        type: "success",
      });
    } catch (error) {
      toast({
        title: "Reset failed",
        description: error.message ?? "Failed to reset settings.",
        type: "error",
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground text-sm">
          System-wide worker idle time configuration.
        </p>
      </div>

      <Card size="sm">
        <CardHeader>
          <CardTitle className="text-sm">Worker Idle Limit</CardTitle>
          <CardDescription>
            A worker is considered idle after this many seconds without activity.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {isLoading ? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-[74px] w-full" />
            </div>
          ) : loadError ? (
            <div className="border-border flex flex-col gap-2 rounded-md border p-4">
              <p className="text-sm text-destructive">Failed to load settings.</p>
              <p className="text-muted-foreground text-xs">
                {loadError.message || "Check that the backend is running."}
              </p>
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="worker-idle-limit">Idle limit (seconds)</Label>
                <Input
                  id="worker-idle-limit"
                  type="number"
                  min={MIN_IDLE_LIMIT}
                  max={MAX_IDLE_LIMIT}
                  value={formValue}
                  onChange={(event) => setFormValue(event.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  Range: {MIN_IDLE_LIMIT}–{MAX_IDLE_LIMIT} seconds (
                  {MIN_IDLE_LIMIT / 60}–{MAX_IDLE_LIMIT / 60} minutes).
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground text-xs">Quick pick:</span>
                {RECOMMENDED_VALUES.map((value) => (
                  <Button
                    key={value}
                    size="sm"
                    variant="outline"
                    onClick={() => setFormValue(String(value))}
                  >
                    {value}s
                  </Button>
                ))}
              </div>

              <div className="border-border flex flex-col gap-1 rounded-md border p-3">
                <span className="text-muted-foreground text-xs">Current</span>
                <span className="font-mono text-sm">
                  {settings.worker_idle_limit} seconds
                </span>
                <span className="text-muted-foreground text-sm">
                  {settings.worker_idle_limit_formatted}
                </span>
                <span className="text-muted-foreground text-xs">
                  Updated {formatRelativeTime(settings.updated_at)}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <Button onClick={handleSave} disabled={isSaving}>
                  Save
                </Button>
                <Button variant="outline" onClick={handleReset} disabled={isSaving}>
                  Reset to defaults
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default Settings;