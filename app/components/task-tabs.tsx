import { useId, useState, type ReactNode } from "react";
export function useTaskTabs(
  names: readonly string[],
  initial: string,
  storageKey?: string,
  onChange?: (name: string) => void,
) {
  const [activeTab, setActiveTab] = useState(() => {
    const saved = storageKey ? sessionStorage.getItem(storageKey) : null;
    return saved && names.includes(saved) ? saved : initial;
  });
  function selectTab(name: string) {
    setActiveTab(name);
    if (storageKey) sessionStorage.setItem(storageKey, name);
    onChange?.(name);
  }
  const id = useId();
  const tabs = (
    <div className="task-tabs" role="tablist" aria-label="Workspace tools">
      {names.map((name, index) => (
        <button
          key={name}
          id={`${id}-${name}`}
          role="tab"
          aria-selected={activeTab === name}
          aria-controls={`${id}-panel`}
          tabIndex={activeTab === name ? 0 : -1}
          onClick={() => selectTab(name)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            event.stopPropagation();
            const nextIndex =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? names.length - 1
                  : (index +
                      (event.key === "ArrowRight" ? 1 : -1) +
                      names.length) %
                    names.length;
            selectTab(names[nextIndex]);
            document.getElementById(`${id}-${names[nextIndex]}`)?.focus();
          }}
        >
          {name}
        </button>
      ))}
    </div>
  );
  return {
    activeTab,
    tabs,
    panelProps: { id: `${id}-panel`, "data-active-task": activeTab },
    label: id,
  };
}
export function Notice({
  children,
  onRetry,
  error = false,
}: {
  children: ReactNode;
  onRetry?: () => void;
  error?: boolean;
}) {
  return (
    <div
      className={`ui-notice${error ? " error" : ""}`}
      role={error ? "alert" : "status"}
    >
      <span>{children}</span>
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  );
}
