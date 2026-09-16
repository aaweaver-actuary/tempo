import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import BuilderView from "../../app/views/analysis_view";
import { Settings } from "../../app/utils/settings";
import { scanGame } from "../../app/lib/game-scan";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: (props: { fen: string; orientation: string; shapes: unknown[]; onMove: (from: string,to: string) => void }) => <div data-testid="board" data-fen={props.fen} data-orientation={props.orientation} data-shapes={JSON.stringify(props.shapes)}><button onClick={() => props.onMove("d2","d4")}>d2d4</button></div> }));
vi.mock("../../app/lib/analysis-engines", () => ({ analyzeWithStockfish: vi.fn(async () => []), analyzeWithMaia: vi.fn(async () => []) }));

it("builder flip preserves repertoire identity and history across remounts", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) => Response.json(String(input).includes("/repertoire/lines") ? { lines: [{ id:"black-line", repertoire_id:"black-repertoire", repertoire_name:"Gambits", trained_color:"black", start_fen:new Chess().fen(), moves:["d2d4","g8f6","0000"] }] } : { annotations: [] })));
  const props = { imported: [], settings: new Settings(), theme:"brown" as const, pieceSet:"cburnett" as const };
  const view = render(<BuilderView {...props} />);
  await waitFor(() => expect((screen.getByRole("combobox",{name:"Active repertoire"}) as unknown as HTMLSelectElement).value).toBe("black-repertoire"));
  fireEvent.change(screen.getByRole("combobox",{name:"Active repertoire"}), { target:{ value:"black-repertoire" } });
  fireEvent.click(screen.getByText("d2d4"));
  const fen=screen.getByTestId("board").getAttribute("data-fen");
  fireEvent.click(screen.getByTitle("Flip board (F)"));
  expect((screen.getByRole("combobox",{name:"Active repertoire"}) as unknown as HTMLSelectElement).value).toBe("black-repertoire");
  expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("white");
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(fen);
  view.unmount(); render(<BuilderView {...props} />);
  await waitFor(() => expect((screen.getByRole("combobox",{name:"Active repertoire"}) as unknown as HTMLSelectElement).value).toBe("black-repertoire"));
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(fen);
  expect(screen.getByText(/Stopped at null move/)).toBeTruthy();
});

it("game scan normalizes black evaluations and identifies missed-punishment opportunities", async () => {
  const cp=[0,150,0];
  const evaluate=vi.fn(async () => [{uci:"e2e4",san:"e4",cp:cp.shift()}]);
  const result=await scanGame(new Chess().fen(),["e4","e5"],"black",evaluate);
  expect(result[1]).toMatchObject({before_cp:-150,after_cp:0,opponent_created_chance:true});
});
