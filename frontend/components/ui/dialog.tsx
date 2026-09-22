// Adapted from shadcn/ui's Dialog component (https://ui.shadcn.com,
// MIT License), which wraps Radix UI's accessible Dialog primitive
// (@radix-ui/react-dialog, MIT License) -- provides the focus trap,
// Escape-to-close, and aria wiring used for the figure lightbox and the
// candidate detail drawer. Restyled for VeriGate's tone tokens. See
// frontend/THIRD_PARTY_NOTICES.md for full attribution.
"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/85" />
      <DialogPrimitive.Content
        className={cn(
          "tone-ink fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-5xl -translate-x-1/2 -translate-y-1/2",
          "border border-rule-strong bg-page shadow-2xl focus:outline-none",
          "max-h-[90dvh] overflow-y-auto",
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          className="absolute right-3 top-3 border border-rule bg-surface p-2 text-mute transition-colors hover:border-rule-strong hover:text-fg motion-reduce:transition-none"
          aria-label="Close"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-2 p-6 pr-14", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn("t-title text-lg text-fg", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("text-sm leading-relaxed text-mute", className)} {...props} />;
}
