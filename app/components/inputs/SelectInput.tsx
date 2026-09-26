import { forwardRef, type SelectHTMLAttributes } from "react";


export const SelectInput = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function SelectInput({ className, ...props }, ref) {
    return <select {...props} ref={ref} className={["ui-select", className].filter(Boolean).join(" ")} />;
  }
);
