import { expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { analyzeWithMaia } from "../../app/lib/analysis-engines";
import { studyRequestSchema, maiaReplySchema } from "../../app/domain/schemas";

it("Maia inference starts in a dedicated worker and validates candidates without occupying board input", async () => {
  const instances: FakeWorker[] = [];
  class FakeWorker {
    onmessage?: (event: MessageEvent) => void;
    messages: {id:number;fen:string;elo:number}[] = [];
    terminate = vi.fn();
    constructor(readonly url: URL) { instances.push(this); }
    postMessage(message: {id:number;fen:string;elo:number}) { this.messages.push(message); }
  }
  vi.stubGlobal("Worker",FakeWorker);
  const position = new Chess();
  const progress = vi.fn();
  const result = analyzeWithMaia(position.fen(),1500,progress);
  expect(instances[0].url.pathname).toContain("maia.worker.ts");
  position.move("e4");
  expect(position.get("e4")?.type).toBe("p");
  const id = instances[0].messages[0].id;
  instances[0].onmessage?.(new MessageEvent("message",{data:{type:"progress",id,progress:50}}));
  expect(progress).toHaveBeenCalledWith(50);
  instances[0].onmessage?.(new MessageEvent("message",{data:{type:"result",id,moves:[
    {uci:"0000",san:"--"},{uci:"e2e4",san:"e4",probability:2},{uci:"e2e4",san:"e4",probability:0.5},
  ]}}));
  expect(await result).toEqual([{uci:"e2e4",san:"e4",probability:0.5}]);
});

it("controlled worker messages reject malformed FEN, null UCI and unexpected structural fields", () => {
  expect(studyRequestSchema.safeParse({id:1,task:{kind:"similarity",fen:"broken",positions:[]}}).success).toBe(false);
  expect(studyRequestSchema.safeParse({id:1,task:{kind:"deck",records:[],deckId:"fork-easy"},unexpected:true}).success).toBe(false);
  expect(maiaReplySchema.safeParse({id:1,type:"result",moves:[],unexpected:true}).success).toBe(false);
});
