import {
  forwardRef,
  cloneElement,
  useId,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type DetailsHTMLAttributes,
  type InputHTMLAttributes,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  type TableHTMLAttributes,
} from "react";

type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";
type ButtonSize = "default" | "compact";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  pending?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "default", pending = false, disabled, className, type = "button", ...props },
  ref,
) {
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      className={["ui-button", className].filter(Boolean).join(" ")}
      data-ui-custom={className ? "" : undefined}
      data-variant={variant}
      data-size={size}
      aria-busy={pending || undefined}
      disabled={disabled || pending}
    />
  );
});

export type IconButtonProps = Omit<ButtonProps, "aria-label"> & {
  "aria-label": string;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, ...props }, ref,
) {
  return <Button {...props} ref={ref} className={["ui-icon-button", className].filter(Boolean).join(" ")} />;
});

export function ActionLink({
  variant = "secondary", size = "default", className, ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant; size?: ButtonSize }) {
  return (
    <a
      {...props}
      className={["ui-button", "ui-action-link", className].filter(Boolean).join(" ")}
      data-variant={variant}
      data-size={size}
    />
  );
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className, ...props }, ref) {
    return <input {...props} ref={ref} className={["ui-input", className].filter(Boolean).join(" ")} />;
  },
);

export const SelectInput = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function SelectInput({ className, ...props }, ref) {
    return <select {...props} ref={ref} className={["ui-select", className].filter(Boolean).join(" ")} />;
  },
);

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function TextArea({ className, ...props }, ref) {
    return <textarea {...props} ref={ref} className={["ui-textarea", className].filter(Boolean).join(" ")} />;
  },
);

export function Field({
  label, hint, error, children, className,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactElement<{ id?: string; "aria-describedby"?: string }>;
  className?: string;
}) {
  const generatedId = useId();
  const controlId = children.props.id ?? generatedId;
  const messageId = hint || error ? `${controlId}-message` : undefined;
  const describedBy = [children.props["aria-describedby"], messageId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={["ui-field", className].filter(Boolean).join(" ")}>
      <label className="ui-field-label" htmlFor={controlId}>{label}</label>
      {cloneElement(children, { id: controlId, "aria-describedby": describedBy })}
      {hint && !error && <small id={messageId} className="ui-field-hint">{hint}</small>}
      {error && <small id={messageId} className="ui-field-error">{error}</small>}
    </div>
  );
}

export function Surface({
  children, className = "", as = "section", ...props
}: HTMLAttributes<HTMLElement> & { as?: "section" | "article" }) {
  const Element = as;
  return <Element {...props} className={["ui-surface", className].filter(Boolean).join(" ")}>{children}</Element>;
}

export function DataTable({ className, ...props }: TableHTMLAttributes<HTMLTableElement>) {
  return <table {...props} className={["ui-table", className].filter(Boolean).join(" ")} />;
}

export function TabList({
  children, label, as = "div", className = "",
}: { children: ReactNode; label: string; as?: "div" | "nav"; className?: string }) {
  const Element = as;
  return (
    <Element
      role="tablist"
      aria-label={label}
      className={["ui-tabs", className].filter(Boolean).join(" ")}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const availableTabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)')];
        const activeIndex = availableTabs.indexOf(document.activeElement as HTMLButtonElement);
        if (activeIndex < 0 || availableTabs.length === 0) return;
        event.preventDefault();
        const nextIndex = event.key === "Home" ? 0 : event.key === "End"
          ? availableTabs.length - 1
          : (activeIndex + (event.key === "ArrowRight" ? 1 : -1) + availableTabs.length) % availableTabs.length;
        availableTabs[nextIndex].focus();
        availableTabs[nextIndex].click();
      }}
    >
      {children}
    </Element>
  );
}

export function ActionMenu({
  label, summaryAriaLabel, menuRole, children, className = "", ...props
}: DetailsHTMLAttributes<HTMLDetailsElement> & { label: string; summaryAriaLabel?: string; menuRole?: "menu" }) {
  return (
    <details
      {...props}
      className={["ui-menu", className].filter(Boolean).join(" ")}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !event.currentTarget.open) return;
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.open = false;
        event.currentTarget.querySelector("summary")?.focus();
      }}
    >
      <summary aria-label={summaryAriaLabel}>{label}</summary>
      <div className="ui-menu-content" role={menuRole}>{children}</div>
    </details>
  );
}

export function Notice({
  children, onRetry, error = false,
}: { children: ReactNode; onRetry?: () => void; error?: boolean }) {
  return (
    <div className={`ui-notice${error ? " error" : ""}`} role={error ? "alert" : "status"}>
      <span>{children}</span>
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
    </div>
  );
}
