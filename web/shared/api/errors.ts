export type AppErrorScope =
  | "turn"
  | "session"
  | "runtime"
  | "settings"
  | "network"
  // KAG 管理面（projects/schema/tasks）与 kag settings 域的请求错误归入
  // 独立 scope，便于错误面板按面聚合。
  | "kag";

export interface AppError {
  code: string;
  message: string;
  retryable: boolean;
  scope: AppErrorScope;
  correlationId?: string;
  status?: number;
}

export class ApiError extends Error {
  readonly appError: AppError;

  constructor(appError: AppError, options?: ErrorOptions) {
    super(appError.message, options);
    this.name = "ApiError";
    this.appError = appError;
  }

  get code(): string {
    return this.appError.code;
  }

  get retryable(): boolean {
    return this.appError.retryable;
  }

  get correlationId(): string | undefined {
    return this.appError.correlationId;
  }

  get status(): number | undefined {
    return this.appError.status;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}
