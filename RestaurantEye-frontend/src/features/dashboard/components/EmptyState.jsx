import { cn } from "@/lib/utils";

function EmptyState({ icon: Icon, title, description, className }) {
  return (
    <div
      className={cn(
        "border-border flex flex-col items-center justify-center gap-2 rounded-md border border-dashed px-4 py-10 text-center",
        className,
      )}
    >
      {Icon && <Icon className="text-muted-foreground size-6" />}
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="text-muted-foreground max-w-xs text-xs">{description}</p>
      )}
    </div>
  );
}

export default EmptyState;
