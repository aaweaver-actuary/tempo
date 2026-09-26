import { forwardRef, type InputHTMLAttributes } from "react";


export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className, ...props }, ref) {
    return <input {...props} ref={ref} className={["ui-input", className].filter(Boolean).join(" ")} />;
  }
);
