import { normalize } from "../legacy/util";

export const MAX_RETRIES = 3;

export class NotFoundError extends Error {
  constructor(id: string) {
    super(`user ${id} not found`);
  }
}

/** Looks up and mutates users. */
export class UserService {
  private cache = new Map<string, string>();

  async login(id: string): Promise<boolean> {
    const key = normalize(id);
    this.audit(key);
    if (!this.cache.has(key)) {
      throw new NotFoundError(id);
    }
    return true;
  }

  audit(id: string): void {
    trace(id);
  }
}

function trace(id: string): void {
  console.log(id);
}
