import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function SectionCard({ title, description, count, children }) {
  return (
    <Card size="sm" className="h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <span>{title}</span>
          {typeof count === "number" && (
            <span className="text-muted-foreground text-xs font-normal tabular-nums">
              {count}
            </span>
          )}
        </CardTitle>
        {description && (
          <p className="text-muted-foreground text-xs">{description}</p>
        )}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export default SectionCard;
