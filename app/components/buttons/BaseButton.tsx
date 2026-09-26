import { forwardRef, type ButtonHTMLAttributes } from "react";
import { ButtonVariant, ButtonSize } from "./types";

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "secondary",
      size = "default",
      pending = false,
      disabled,
      className,
      type = "button",
      ...props
    },
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
  },
);
export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  pending?: boolean;
};
