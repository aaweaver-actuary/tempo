import { TabList } from "./ui";
import { Button } from "./buttons/BaseButton";
import { useId, useState } from "react";
export { Notice } from "./Notice";
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
    <TabList className="task-tabs" label="Workspace tools">
      {names.map((name, index) => (
        <Button
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
        </Button>
      ))}
    </TabList>
  );
  return {
    activeTab,
    tabs,
    panelProps: { id: `${id}-panel`, "data-active-task": activeTab },
    label: id,
  };
}
