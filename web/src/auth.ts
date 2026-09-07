import { DrizzleAdapter } from '@auth/drizzle-adapter';
import NextAuth, { type NextAuthConfig } from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import GitHub from 'next-auth/providers/github';

import { and, eq, isNull } from 'drizzle-orm';

import { db, schema } from '@/db';
import { authenticate } from '@/lib/accounts';
import { claimInvitations } from '@/lib/projects';

/**
 * Authentication.
 *
 * GitHub is the only real provider: this is a tool for people who already have
 * a GitHub account, and not owning a password store is a feature.
 *
 * The second is email and password, for people who do not have a GitHub
 * account or do not want to use it here. Everything that makes a password
 * usable safely -- how it is hashed, what is refused, how guessing is slowed
 * down -- is in `lib/password.ts` and `lib/accounts.ts`; this file only wires
 * them in.
 */

const providers = [
  ...(process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET
    ? [
        GitHub({
          clientId: process.env.AUTH_GITHUB_ID,
          clientSecret: process.env.AUTH_GITHUB_SECRET,
        }),
      ]
    : []),

  Credentials({
    id: 'password',
    name: 'Email and password',
    credentials: {
      email: { label: 'Email', type: 'email' },
      password: { label: 'Password', type: 'password' },
    },
    async authorize(credentials) {
      return authenticate(String(credentials?.email ?? ''), String(credentials?.password ?? ''));
    },
  }),
];

/**
 * The address the application is reached at, told to Auth.js.
 *
 * `trustHost` below stops the refusal, but it does not settle what the links
 * Auth.js builds point at, and the standalone server does not help: it makes
 * every request URL out of the address it is bound to, so callbacks come out
 * as `http://0.0.0.0:3000/...` whatever host the request carried. Harmless for
 * a password sign-in, which posts to a relative path; fatal for GitHub, whose
 * callback has to match the one registered.
 *
 * `SITE_URL` already exists, is already required by the installation, and is
 * already checked before anything starts -- so it is the answer, and a better
 * one than the request's host: it is fixed by whoever installed this rather
 * than by whoever sent the request.
 *
 * Left alone when it is not set, which is the hosted deployment: there Auth.js
 * works the address out from Vercel's own environment.
 */
if (!process.env.AUTH_URL && process.env.SITE_URL) {
  process.env.AUTH_URL = new URL('/api/auth', process.env.SITE_URL).toString();
}

/**
 * Exported so it can be asserted on. The one setting below that has no visible
 * effect anywhere it is developed or tested is the one that breaks every
 * installation, so it is checked rather than trusted to stay.
 */
export const config: NextAuthConfig = {
  adapter: DrizzleAdapter(db, {
    usersTable: schema.users,
    accountsTable: schema.accounts,
    sessionsTable: schema.sessions,
    verificationTokensTable: schema.verificationTokens,
  }),
  providers,
  /**
   * Believe the host the request arrived on.
   *
   * Auth.js works this out from the environment and gets it right everywhere
   * except the one that matters here: it trusts the host in development, and
   * on Vercel, and nowhere else. A production build served from a container
   * is neither, so without this every request to `/api/auth/*` is refused as
   * `UntrustedHost` -- which reaches the browser as "There is a problem with
   * the server configuration" and says nothing about the host at all.
   *
   * It is invisible until it is fatal: `npm run dev` trusts the host, CI never
   * builds a running server, and Vercel sets its own flag. The first place it
   * appears is somebody else's installation, at their first sign-in.
   *
   * What makes it safe to believe is that nothing is redirected to on the
   * strength of it: Auth.js only ever redirects to a relative path or to its
   * own origin, so a forged Host cannot send a sign-in somewhere else. Put the
   * application behind a proxy that sets Host, which is what `SITE_URL`
   * describes and what the install instructions ask for.
   */
  trustHost: true,
  // The credentials provider cannot use database sessions, so both strategies
  // would otherwise be in play at once. JWT keeps one code path.
  session: { strategy: 'jwt' },
  pages: { signIn: '/sign-in' },
  callbacks: {
    jwt({ token, user }) {
      if (user?.id) token.sub = user.id;
      return token;
    },
    session({ session, token }) {
      if (token.sub) session.user.id = token.sub;
      return session;
    },
  },
  events: {
    /**
     * Someone can be invited to a project before they have an account, so this
     * is the first moment those invitations have a user to attach themselves
     * to. A failure here must not stop the sign-in: the invitation is still in
     * the table and the next sign-in will find it.
     */
    async signIn({ user, account }) {
      if (!user.id || !user.email) return;

      try {
        // An OAuth provider only hands over an address it has verified, so
        // arriving through one is the proof. Recorded rather than assumed at
        // every read, so that the rest of the application has one question to
        // ask: is this address verified?
        if (account && account.provider !== 'password') {
          await db
            .update(schema.users)
            .set({ emailVerified: new Date() })
            .where(and(eq(schema.users.id, user.id), isNull(schema.users.emailVerified)));
        }

        await claimInvitations(user.id, user.email);
      } catch (error) {
        console.error('could not claim invitations', error);
      }
    },
  },
};

export const { handlers, signIn, signOut, auth } = NextAuth(config);

/** Whether GitHub sign-in is configured; the sign-in page adapts to it. */
export const githubEnabled = Boolean(
  process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET,
);
/** Whether the sign-in page offers email and password. Always: it needs no setup. */
export const passwordSignInEnabled = true;
