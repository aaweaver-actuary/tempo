import { expect, it } from "vitest";
import { studyRequestSchema, maiaReplySchema } from "../../app/domain/schemas";

it("controlled worker messages reject malformed FEN, null UCI and unexpected structural fields", () => {
  expect(studyRequestSchema.safeParse({id:1,task:{kind:"similarity",fen:"broken",positions:[]}}).success).toBe(false);
  expect(studyRequestSchema.safeParse({id:1,task:{kind:"deck",records:[],deckId:"fork-easy"},unexpected:true}).success).toBe(false);
  expect(maiaReplySchema.safeParse({id:1,type:"result",moves:[],unexpected:true}).success).toBe(false);
});
