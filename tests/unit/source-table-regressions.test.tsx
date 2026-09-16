import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import CandidateMovesTable from "../../app/CandidateMovesTable";
import { asNonNegativeInteger, asSanMove, asUciMove } from "../../app/types";

it("source rows show frequency, WDL, practical score and support keyboard preview",()=>{
  const onHover=vi.fn(),onPlay=vi.fn();
  render(<CandidateMovesTable moves={[{uci:asUciMove("e2e4"),san:asSanMove("e4"),white:asNonNegativeInteger(40),draws:asNonNegativeInteger(30),black:asNonNegativeInteger(30)}]} totalGames={1000} covered={new Set()} detail="results" turn="black" onHover={onHover} onPlay={onPlay}/>);
  expect(screen.getByText(/10% frequency · 40W · 30D · 30L · Black score 45%/)).toBeTruthy();
  const row=screen.getByRole("button");fireEvent.focus(row);expect(onHover).toHaveBeenCalledWith("e2e4");
  fireEvent.click(row);expect(onPlay).toHaveBeenCalledWith("e2e4");
  fireEvent.blur(row);expect(onHover).toHaveBeenCalledWith(null);
});
