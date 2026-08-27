import { Cctv } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function VideoFeedPlaceholder({ cameraId }) {
  return (
    <Card size="sm" className="h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <span>Live Feed</span>
          <Badge variant="outline" className="font-mono">
            {cameraId}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="bg-muted border-border flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-md border border-dashed">
          <Cctv className="text-muted-foreground size-8" />
          <p className="text-sm font-medium">No stream yet</p>
          <p className="text-muted-foreground text-xs">
            The live stream for {cameraId} will render here.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export default VideoFeedPlaceholder;
