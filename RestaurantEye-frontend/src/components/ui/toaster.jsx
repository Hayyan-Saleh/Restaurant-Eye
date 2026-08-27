import {
  CircleCheckIcon,
  InfoIcon,
  Loader2Icon,
  OctagonXIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import {
  Toast,
  ToastAction,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from "@/components/ui/toast";
function ToastIcon({ type }) {
  let icon = null;

  if (type === "success") {
    icon = <CircleCheckIcon aria-hidden="true" />;
  }

  if (type === "info") {
    icon = <InfoIcon aria-hidden="true" />;
  }

  if (type === "warning") {
    icon = <TriangleAlertIcon aria-hidden="true" />;
  }

  if (type === "error") {
    icon = <OctagonXIcon className="text-destructive" aria-hidden="true" />;
  }

  if (type === "loading") {
    icon = <Loader2Icon className="animate-spin" aria-hidden="true" />;
  }

  if (!icon) return null;

  return (
    <span
      data-slot="toast-icon"
      className="shrink-0 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4"
    >
      {icon}
    </span>
  );
}

function Toaster() {
  const { toasts } = useToast();

  return (
    <ToastProvider>
      <ToastViewport>
        {toasts.map(({ id, type, title, description, action, ...props }) => (
          <Toast key={id} {...props}>
            <div className="flex w-full items-center gap-3 overflow-hidden">
              <ToastIcon type={type} />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                {title && <ToastTitle>{title}</ToastTitle>}
                {description && <ToastDescription>{description}</ToastDescription>}
              </div>
              {action && <ToastAction altText={action.altText}>{action.label}</ToastAction>}
            </div>
            <ToastClose />
          </Toast>
        ))}
      </ToastViewport>
    </ToastProvider>
  );
}

export { Toaster };