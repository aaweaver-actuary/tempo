import { forwardRef } from "react";
import { Button, ButtonProps } from "./BaseButton";


export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, ...props }, ref
) {
  return <Button {...props} ref={ref} className={["ui-icon-button", className].filter(Boolean).join(" ")} />;
});export type IconButtonProps = Omit<ButtonProps, "aria-label"> & {
  "aria-label": string;
};

