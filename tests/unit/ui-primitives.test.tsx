import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  ActionLink,
  ActionMenu,
  DataTable,
  Field,
  TabList,
  TextArea,
} from "../../app/components/ui";
import { SelectInput } from "@/app/components/inputs/SelectInput";
import { TextInput } from "@/app/components/inputs/TextInput";
import { IconButton } from "@/app/components/buttons/IconButton";
import { Button } from "@/app/components/buttons/BaseButton";

it("shared buttons preserve native actions, state, and accessible names", () => {
  const onClick = vi.fn();
  render(<>
    <Button variant="primary" onClick={onClick}>Save</Button>
    <Button pending onClick={onClick}>Saving</Button>
    <IconButton aria-label="Close dialog">×</IconButton>
    <ActionLink href="/help" variant="quiet">Help</ActionLink>
  </>);
  const save = screen.getByRole("button", { name: "Save" });
  expect(save.getAttribute("type")).toBe("button");
  expect(save.getAttribute("data-variant")).toBe("primary");
  fireEvent.click(save);
  expect(onClick).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Saving" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Saving" }).getAttribute("aria-busy")).toBe("true");
  expect(screen.getByRole("button", { name: "Close dialog" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Help" }).getAttribute("href")).toBe("/help");
});

it("shared tab and menu wrappers keep keyboard navigation and named actions", () => {
  const selectSecond = vi.fn();
  render(<>
    <TabList label="Sections">
      <Button role="tab" aria-selected="true">First</Button>
      <Button role="tab" aria-selected="false" onClick={selectSecond}>Second</Button>
    </TabList>
    <ActionMenu label="More" summaryAriaLabel="More actions"><Button>Export</Button></ActionMenu>
  </>);
  const first = screen.getByRole("tab", { name: "First" });
  first.focus();
  fireEvent.keyDown(first, { key: "ArrowRight" });
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Second" }));
  expect(selectSecond).toHaveBeenCalledTimes(1);
  expect(screen.getByText("More").getAttribute("aria-label")).toBe("More actions");
  expect(screen.getByRole("button", { name: "Export" })).toBeTruthy();
  const summary = screen.getByText("More");
  fireEvent.click(summary);
  fireEvent.keyDown(summary, { key: "Escape" });
  expect(summary.closest("details")?.open).toBe(false);
});

it("shared fields and tables preserve native labels, values, and semantics", () => {
  render(<>
    <Field label="Search" hint="Search openings"><TextInput defaultValue="Spanish" /></Field>
    <Field label="Group"><SelectInput defaultValue="a"><option value="a">A</option></SelectInput></Field>
    <Field label="Notes"><TextArea defaultValue="Remember this" /></Field>
    <DataTable><caption>Results</caption><tbody><tr><td>One</td></tr></tbody></DataTable>
  </>);
  expect(screen.getByRole("textbox", { name: "Search" }).getAttribute("value")).toBe("Spanish");
  expect(screen.getByRole("combobox", { name: "Group" })).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "Notes" })).toBeTruthy();
  expect(screen.getByRole("table", { name: "Results" })).toBeTruthy();
});
