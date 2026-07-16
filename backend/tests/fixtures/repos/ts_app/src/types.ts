export interface Handler {
  handle(event: string): void;
  name: string;
}

export type Result = { ok: boolean } | null;

export enum Color {
  Red,
  Green,
}
