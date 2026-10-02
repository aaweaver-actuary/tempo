import { useLayoutEffect, useRef, useSyncExternalStore, type RefObject } from "react";

export type BoardKeyboardActions = {
  // History boards own navigation even at a boundary; static previews do not.
  capturesNavigation?: boolean;
  previous?: () => void;
  next?: () => void;
  start?: () => void;
  end?: () => void;
  reset?: () => void;
  hint?: () => void;
  nextItem?: () => void;
  defaultOrientation?: "white" | "black";
  defaultActive?: boolean;
};

type BoardRegistration = { element: HTMLElement; current: () => BoardKeyboardActions & { flip: () => void; help: () => void } };
type PopupRegistration = { element: HTMLElement; close: () => void; passive: boolean; priority: () => number; opener: HTMLElement | null };
const boards: BoardRegistration[] = [];
const popups: PopupRegistration[] = [];
let activeBoard: BoardRegistration | undefined;
let removeDispatcher: (() => void) | undefined;
const preferenceListeners = new Set<() => void>();
let unpersistedLetterPreference: boolean | undefined;
export const LETTER_SHORTCUTS_KEY = "tempo-letter-shortcuts";

export function letterShortcutsEnabled() {
  if (unpersistedLetterPreference !== undefined) return unpersistedLetterPreference;
  try { return localStorage.getItem(LETTER_SHORTCUTS_KEY) !== "false"; }
  catch { return true; }
}
export function setLetterShortcutsEnabled(enabled: boolean) {
  try { localStorage.setItem(LETTER_SHORTCUTS_KEY, String(enabled)); unpersistedLetterPreference = undefined; }
  catch { unpersistedLetterPreference = enabled; }
  preferenceListeners.forEach(listener => listener());
}
export function useLetterShortcutsEnabled() {
  return useSyncExternalStore(listener => { preferenceListeners.add(listener); return () => { preferenceListeners.delete(listener); }; }, letterShortcutsEnabled, () => true);
}

function isVisibleElement(element: HTMLElement) {
  if (!element.isConnected || element.closest('[hidden],[inert],[aria-hidden="true"],details:not([open])')) return false;
  if (getComputedStyle(element).visibility === "hidden") return false;
  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    if (getComputedStyle(ancestor).display === "none") return false;
  }
  return true;
}
function availableBoard(board: BoardRegistration) {
  return isVisibleElement(board.element) && board.element.getClientRects().length > 0 &&
    !board.element.closest('[data-unavailable="true"]');
}
function topPopup(passive: boolean) {
  const eligible = popups.filter(popup => popup.passive === passive && isVisibleElement(popup.element));
  return passive ? eligible.toSorted((left, right) => left.priority() - right.priority())[0]
    : eligible.findLast(popup => !eligible.some(child => child !== popup && popup.element.contains(child.element)));
}
function keyboardControl(target: EventTarget | null) {
  return target instanceof HTMLElement && Boolean(target.isContentEditable || target.closest(
    'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="tablist"],[role="separator"],[role="slider"],[role="spinbutton"],[role="combobox"],[role="listbox"],[role="menu"]',
  ));
}
function selectedBoard() {
  const popup = topPopup(false);
  const eligible = boards.filter(board => availableBoard(board) && (!popup || popup.element.contains(board.element)));
  // A dialog which has not yet registered must still fence the background board.
  const unregisteredDialog = [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(dialog => isVisibleElement(dialog) && !popups.some(entry => entry.element === dialog));
  const scoped = unregisteredDialog ? eligible.filter(board => unregisteredDialog.contains(board.element)) : eligible;
  return scoped.find(board => board === activeBoard) ?? scoped.find(board => board.current().defaultActive) ?? scoped[0];
}
function dispatchKey(event: KeyboardEvent) {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "Escape") {
    if (event.repeat || event.shiftKey) return;
    const popup = topPopup(false) ?? topPopup(true);
    if (!popup) return;
    event.preventDefault(); event.stopPropagation();
    const previouslyFocused = document.activeElement;
    popup.close();
    if (!popup.passive && popup.opener?.isConnected && document.activeElement === previouslyFocused) popup.opener.focus();
    return;
  }
  if (keyboardControl(event.target) || window.getSelection()?.isCollapsed === false) return;
  const key = event.key.toLowerCase();
  if (event.shiftKey && key !== "?" && !["f", "r", "h", "n"].includes(key)) return;
  if (["f", "r", "h", "n"].includes(key) && !letterShortcutsEnabled()) return;
  const board = selectedBoard();
  if (!board) return;
  const actions = board.current();
  const navigationKey = ["arrowleft", "arrowright", "arrowup", "arrowdown", "home", "end"].includes(key);
  if (navigationKey && !actions.capturesNavigation) return;
  const command = ({ arrowleft: actions.previous, arrowright: actions.next,
    arrowup: actions.start, home: actions.start, arrowdown: actions.end, end: actions.end,
    f: actions.flip, r: actions.reset, h: actions.hint, n: actions.nextItem, "?": actions.help } as Record<string, (() => void) | undefined>)[key];
  if (!command) {
    if (navigationKey) event.preventDefault();
    return;
  }
  event.preventDefault();
  if (event.repeat && !navigationKey) return;
  command();
}
function ensureDispatcher() {
  if (removeDispatcher) return;
  const selectBoard = (event: Event) => {
    const target = event.target;
    if (!(target instanceof Node)) return;
    const board = boards.findLast(candidate => {
      const scope = candidate.element.closest('[data-board-keyboard-scope]') ?? candidate.element;
      return scope.contains(target) && availableBoard(candidate);
    });
    if (board) activeBoard = board;
  };
  const flipBoard = () => selectedBoard()?.current().flip();
  const showHelp = () => selectedBoard()?.current().help();
  const preferenceChanged = (event: StorageEvent) => {
    if (event.key === LETTER_SHORTCUTS_KEY || event.key === null) {
      unpersistedLetterPreference = undefined;
      preferenceListeners.forEach(listener => listener());
    }
  };
  window.addEventListener("keydown", dispatchKey);
  window.addEventListener("pointerdown", selectBoard, true);
  window.addEventListener("focusin", selectBoard, true);
  window.addEventListener("tempo:flip-board", flipBoard);
  window.addEventListener("tempo:board-help", showHelp);
  window.addEventListener("storage", preferenceChanged);
  removeDispatcher = () => {
    window.removeEventListener("keydown", dispatchKey);
    window.removeEventListener("pointerdown", selectBoard, true);
    window.removeEventListener("focusin", selectBoard, true);
    window.removeEventListener("tempo:flip-board", flipBoard);
    window.removeEventListener("tempo:board-help", showHelp);
    window.removeEventListener("storage", preferenceChanged);
  };
}
function releaseDispatcher() {
  if (boards.length || popups.length) return;
  removeDispatcher?.(); removeDispatcher = undefined; activeBoard = undefined;
}
export function useBoardKeyboard(elementRef: RefObject<HTMLElement | null>, actions: BoardKeyboardActions & { flip: () => void; help: () => void }) {
  const committedActions = useRef(actions);
  useLayoutEffect(() => { committedActions.current = actions; });
  useLayoutEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    const registration = { element, current: () => committedActions.current };
    boards.push(registration); ensureDispatcher();
    return () => { boards.splice(boards.indexOf(registration), 1); if (activeBoard === registration) activeBoard = undefined; releaseDispatcher(); };
  }, [elementRef]);
}
export function usePopupKeyboard(elementRef: RefObject<HTMLElement | null>, onClose: () => void, enabled = true, passive = false, priority = 0) {
  const closeRef = useRef(onClose);
  const priorityRef = useRef(priority);
  useLayoutEffect(() => { closeRef.current = onClose; priorityRef.current = priority; });
  useLayoutEffect(() => {
    const element = elementRef.current;
    if (!enabled || !element) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const registration = { element, close: () => closeRef.current(), passive, priority: () => priorityRef.current, opener };
    const openedDetails = () => {
      if (!(element instanceof HTMLDetailsElement) || !element.open) return;
      registration.opener = element.querySelector("summary");
      popups.splice(popups.indexOf(registration), 1); popups.push(registration);
    };
    popups.push(registration); ensureDispatcher();
    element.addEventListener("toggle", openedDetails);
    return () => { element.removeEventListener("toggle", openedDetails); popups.splice(popups.indexOf(registration), 1); releaseDispatcher(); };
  }, [elementRef, enabled, passive]);
}

export function historyKeyboardActions(cursor: number, length: number, navigate: (cursor: number) => void): BoardKeyboardActions {
  return {
    capturesNavigation: true,
    previous: cursor > 0 ? () => navigate(Math.max(0, cursor - 1)) : undefined,
    next: cursor < length ? () => navigate(Math.min(length, cursor + 1)) : undefined,
    start: () => navigate(0), end: () => navigate(length),
  };
}
