// Sign-in against the Gateway (/auth/*). The session itself is an HttpOnly
// cookie the browser manages -- nothing here can read or store the token.

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  role: string;
}

export interface AuthStatus {
  /** "none": the Gateway runs without login (single shared account). */
  auth_mode: 'none' | 'owui';
  authenticated: boolean;
  user: AuthUser | null;
}

export type LoginFailure = 'invalid' | 'rate_limited' | 'unreachable';

export class LoginError extends Error {
  constructor(
    readonly kind: LoginFailure,
    readonly retryAfterSeconds?: number,
  ) {
    super(kind);
  }
}

/** What the session store needs from authentication (the seam for tests). */
export interface AuthPort {
  me(): Promise<AuthStatus>;
  login(email: string, password: string): Promise<AuthUser>;
  logout(): Promise<void>;
}

export class AuthClient implements AuthPort {
  constructor(
    private readonly baseUrl: string,
    private readonly fetchImpl: typeof fetch = (...args) => fetch(...args),
  ) {}

  async me(): Promise<AuthStatus> {
    const res = await this.fetchImpl(`${this.baseUrl}/auth/me`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`GET /auth/me -> ${res.status}`);
    return (await res.json()) as AuthStatus;
  }

  async login(email: string, password: string): Promise<AuthUser> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}/auth/login`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
    } catch {
      throw new LoginError('unreachable');
    }
    if (res.ok) return ((await res.json()) as { user: AuthUser }).user;
    if (res.status === 401) throw new LoginError('invalid');
    if (res.status === 429) {
      const wait = Number(res.headers.get('Retry-After'));
      throw new LoginError('rate_limited', Number.isFinite(wait) && wait > 0 ? wait : undefined);
    }
    throw new LoginError('unreachable');
  }

  async logout(): Promise<void> {
    await this.fetchImpl(`${this.baseUrl}/auth/logout`, { method: 'POST', credentials: 'same-origin' });
  }
}
