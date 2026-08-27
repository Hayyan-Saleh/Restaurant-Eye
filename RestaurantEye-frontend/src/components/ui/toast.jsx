import {
  Toast as ToastRoot,
  ToastAction as ToastActionPrimitive,
  ToastClose as ToastClosePrimitive,
  ToastDescription as ToastDescriptionPrimitive,
  ToastProvider as ToastProviderPrimitive,
  ToastTitle as ToastTitlePrimitive,
  ToastViewport as ToastViewportPrimitive,
} from "@radix-ui/react-toast";
import { XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const ToastProvider = ToastProviderPrimitive;

function ToastViewport({ className, ...props }) {
  return (
    <ToastViewportPrimitive
      data-slot="toast-viewport"
      className={cn(
        "fixed top-0 z-[100] flex max-h-screen w-full flex-col-reverse p-4 sm:bottom-0 sm:right-0 sm:top-auto sm:flex-col md:max-w-[420px]",
        className,
      )}
      {...props}
    />
  );
}

function Toast({ className, ...props }) {
  return (
    <ToastRoot
      data-slot="toast"
      className={cn(
        "group pointer-events-auto relative flex w-full items-center justify-between gap-2 overflow-hidden rounded-md border bg-popover p-4 text-popover-foreground shadow-lg transition-all data-[swipe=cancel]:translate-x-0 data-[swipe=end]:translate-x-[var(--radix-toast-swipe-end-x)] data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)] data-[swipe=move]:transition-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[swipe=end]:animate-out data-[state=closed]:fade-out-80 data-[state=closed]:slide-out-to-right-full data-[state=open]:slide-in-from-top-full data-[state=open]:sm:slide-in-from-bottom-full",
        className,
      )}
      {...props}
    />
  );
}

function ToastTitle({ className, ...props }) {
  return (
    <ToastTitlePrimitive
      data-slot="toast-title"
      className={cn("text-sm font-medium", className)}
      {...props}
    />
  );
}

function ToastDescription({ className, ...props }) {
  return (
    <ToastDescriptionPrimitive
      data-slot="toast-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

function ToastAction({ className, ...props }) {
  return (
    <ToastActionPrimitive
      data-slot="toast-action"
      className={cn(
        "shrink-0 rounded-md text-sm font-medium ring-offset-background transition-colors hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

function ToastClose({ className, ...props }) {
  return (
    <ToastClosePrimitive
      data-slot="toast-close"
      asChild
      className={cn(
        "rounded-md ring-offset-background transition-opacity hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
        className,
      )}
      {...props}
    >
      <Button variant="ghost" size="icon" aria-label="Close toast">
        <XIcon className="size-4" />
      </Button>
    </ToastClosePrimitive>
  );
}

export {
  ToastProvider,
  ToastViewport,
  Toast,
  ToastTitle,
  ToastDescription,
  ToastAction,
  ToastClose,
};