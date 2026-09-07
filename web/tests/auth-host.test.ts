/**
 * Whether Auth.js believes the host it was reached on.
 *
 * This has one setting and no visible effect anywhere it is developed: Auth.js
 * trusts the host in development and on Vercel, so `npm run dev`, CI and the
 * hosted deployment all pass whatever it is set to. The first place it shows
 * is a production build served from a container -- someone else's server, at
 * their first sign-in, as "There is a problem with the server configuration".
 *
 * So this asks the handler rather than reading the setting: a request arrives
 * on a host, and either it is answered or it is refused.
 */

import { NextRequest } from 'next/server';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestDatabase, type TestDatabase } from './db';

const holder = vi.hoisted(() => ({ db: null as unknown as TestDatabase }));

vi.mock('@/db', async () => {
  const schema = await import('@/db/schema');
  return {
    get db() {
      return holder.db;
    },
    schema,
  };
});

beforeAll(async () => {
  // The adapter is built as the module loads and looks at what it is given, so
  // a stub will not do. Nothing here queries it.
  holder.db = await createTestDatabase();
});

describe('a request arriving on a host', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('is answered rather than refused as untrusted', async () => {
    // What the container is, and what the hosted deployment is not.
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('VERCEL', '');
    vi.stubEnv('AUTH_TRUST_HOST', '');

    const { handlers } = await import('@/auth');
    const response = await handlers.GET(
      new NextRequest('http://cad.internal.example/api/auth/providers'),
    );

    expect(response.status).toBe(200);
    // The refusal is a redirect to the error page rather than a status, so the
    // body is what tells the two apart.
    expect(await response.json()).toHaveProperty('password');

    vi.unstubAllEnvs();
  });

  it('is the same host the response is built for', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('VERCEL', '');
    vi.stubEnv('AUTH_TRUST_HOST', '');

    const { handlers } = await import('@/auth');
    const response = await handlers.GET(
      new NextRequest('http://cad.internal.example/api/auth/providers'),
    );
    const providers = (await response.json()) as Record<string, { callbackUrl?: string }>;

    // Not localhost, and not whatever the build machine was called.
    expect(providers.password?.callbackUrl).toContain('cad.internal.example');

    vi.unstubAllEnvs();
  });
});

describe('the address the links are built for', () => {
  beforeEach(() => {
    vi.resetModules();
    delete process.env.AUTH_URL;
  });

  it('is SITE_URL, not the address the server is bound to', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('VERCEL', '');
    vi.stubEnv('SITE_URL', 'https://cad.internal.example');

    const { handlers } = await import('@/auth');
    const response = await handlers.GET(
      // What the standalone server passes on: the address it listens on,
      // rather than the one anybody typed.
      new NextRequest('http://0.0.0.0:3000/api/auth/providers'),
    );
    const providers = (await response.json()) as Record<string, { callbackUrl?: string }>;

    expect(providers.password?.callbackUrl).toBe(
      'https://cad.internal.example/api/auth/callback/password',
    );

    vi.unstubAllEnvs();
    delete process.env.AUTH_URL;
  });

  it('is AUTH_URL when someone set that instead', async () => {
    // Both are ways of saying the same thing, and the more specific one wins.
    // Somebody reaching for AUTH_URL is reaching past SITE_URL on purpose.
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('VERCEL', '');
    vi.stubEnv('SITE_URL', 'https://cad.internal.example');
    process.env.AUTH_URL = 'https://sso.internal.example/api/auth';

    const { handlers } = await import('@/auth');
    const response = await handlers.GET(
      new NextRequest('http://0.0.0.0:3000/api/auth/providers'),
    );
    const providers = (await response.json()) as Record<string, { callbackUrl?: string }>;

    expect(providers.password?.callbackUrl).toBe(
      'https://sso.internal.example/api/auth/callback/password',
    );

    vi.unstubAllEnvs();
    delete process.env.AUTH_URL;
  });

  it('is left to Auth.js when SITE_URL says nothing', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('VERCEL', '');
    vi.stubEnv('SITE_URL', '');

    const { handlers } = await import('@/auth');
    const response = await handlers.GET(
      new NextRequest('https://hosted.example/api/auth/providers'),
    );
    const providers = (await response.json()) as Record<string, { callbackUrl?: string }>;

    expect(providers.password?.callbackUrl).toContain('hosted.example');

    vi.unstubAllEnvs();
  });
});
