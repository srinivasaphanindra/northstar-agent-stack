export class AppError extends Error {
  constructor(public code: "unauthorized" | "forbidden" | "not_found" | "invalid" | "conflict" | "limit", message: string) {
    super(message);
  }
}
export const isAppError = (e: unknown): e is AppError => e instanceof AppError;
