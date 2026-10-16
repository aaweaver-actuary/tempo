export type StockfishRecord = {model: 'StockfishJob' | 'StockfishResult' | 'JobStatus' | 'CancellationRequest' | 'CancellationAcknowledgement'; payload: Record<string, unknown>};
export declare function stockfishInputDigest(job: Record<string, unknown>): string;
export declare function inspectEngine(): Promise<{engine_version: string; implementation_version: string; runtime_name: string; runtime_version: string}>;
export declare class StockfishWorker {
  constructor(options?: Record<string, unknown>);
  start(): Promise<unknown>;
  receive(record: StockfishRecord): void;
  close(options?: {drain?: boolean}): Promise<void>;
  on(event: 'record' | 'diagnostic', listener: (record: any) => void): this;
}
