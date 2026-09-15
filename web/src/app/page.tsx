import Link from 'next/link';
import { redirect } from 'next/navigation';

import { ModelList } from '@/components/catalogue/ModelList';
import { SignOutButton } from '@/components/auth/SignOutButton';
import { VerifyBanner } from '@/components/auth/VerifyBanner';
import {
  PAGE_SIZE,
  countModels,
  decodeCursor,
  encodeCursor,
  pageOfModels,
  type Page,
} from '@/lib/catalogue';
import { MODE_NAMES, SUPPORTED_FORMAT_NAMES } from '@/lib/formats';
import { deletableIds } from '@/lib/models';
import { projectsFor } from '@/lib/projects';
import {
  currentUser,
  emailVerified,
  personalProject,
  readableProjects,
  writableProjects,
} from '@/lib/session';

export const dynamic = 'force-dynamic';

/**
 * The catalogue.
 *
 * Reads the database directly rather than through the API: it is a server
 * component in the same process, and the round trip would buy nothing.
 */
async function loadPage(
  userId: string,
  cursors: { after?: string; before?: string },
): Promise<{ page: Page; total: number }> {
  // Make sure the user has somewhere to upload to, then read what they can
  // see -- not just that one project, or a model shared with them would be
  // openable by URL but invisible in the catalogue.
  await personalProject(userId);
  const projectIds = await readableProjects(userId);

  const [page, total] = await Promise.all([
    pageOfModels({
      projectIds,
      after: decodeCursor(cursors.after),
      before: decodeCursor(cursors.before),
    }),
    countModels(projectIds),
  ]);

  return { page, total };
}

function NotConfigured({ detail }: { detail: string }) {
  return (
    <div className="mx-auto max-w-xl px-6 py-16">
      <h2 className="text-base font-medium text-slate-900">Not configured yet</h2>
      <p className="pt-2 text-sm text-slate-600">
        The catalogue needs a database and object storage. Copy{' '}
        <code className="rounded bg-slate-100 px-1">.env.example</code> to{' '}
        <code className="rounded bg-slate-100 px-1">.env.local</code>, fill it in, then run{' '}
        <code className="rounded bg-slate-100 px-1">npm run db:migrate</code>.
      </p>
      <pre className="mt-4 overflow-x-auto rounded bg-slate-100 p-3 text-xs text-slate-700">
        {detail}
      </pre>
      <p className="pt-4 text-sm text-slate-600">
        The viewer itself needs neither:{' '}
        <Link href="/sample" className="text-blue-600 hover:underline">
          open the bundled sample
        </Link>
        .
      </p>
    </div>
  );
}

function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export default async function Home({
  searchParams,
}: {
  // Which page is in the address, so it survives a refresh, a bookmark and
  // the three-second poll the list runs while something is converting.
  searchParams: Promise<{ after?: string; before?: string }>;
}) {
  const cursors = await searchParams;

  // redirect() reports itself by throwing, so it stays outside every try
  // block: caught, it would turn "please sign in" into "not configured".
  let user: Awaited<ReturnType<typeof currentUser>>;
  try {
    user = await currentUser();
  } catch (cause) {
    return (
      <main className="min-h-dvh bg-slate-50">
        <NotConfigured detail={describe(cause)} />
      </main>
    );
  }

  if (!user) redirect('/sign-in');

  let page: Page;
  let total: number;
  let projects: Awaited<ReturnType<typeof projectsFor>>;
  let destinations: { id: string; name: string }[];
  let deletable: string[];
  let verified = true;
  try {
    ({ page, total } = await loadPage(user.id, cursors));
    // Asked about this page's models only, which is the other half of what
    // paging bought: it was two queries for the whole catalogue before.
    deletable = [...(await deletableIds(page.models, user.id))];
    projects = await projectsFor(user.id);
    destinations = (await writableProjects(user.id)).map((project) => ({
      id: project.id,
      name: project.name,
    }));
    verified = await emailVerified(user.id);
  } catch (cause) {
    return (
      <main className="min-h-dvh bg-slate-50">
        <NotConfigured detail={describe(cause)} />
      </main>
    );
  }

  return (
    <main className="min-h-dvh bg-slate-50">
      {/* Stays put while a long catalogue scrolls: the way back and the way
          out should not depend on where you are in the list. */}
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <Link href="/" className="flex items-center gap-2.5">
            {/* The same isometric box as the favicon, so the tab and the page
                are recognisably one thing. */}
            <svg viewBox="0 0 32 32" aria-hidden className="h-6 w-6">
              <rect width="32" height="32" rx="7" fill="#2563eb" />
              <g
                fill="none"
                stroke="#ffffff"
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
              >
                <path d="M16 6 26 11.5 26 20.5 16 26 6 20.5 6 11.5 Z" />
                <path d="M6 11.5 16 17 26 11.5" />
                <path d="M16 17 16 26" />
              </g>
            </svg>
            <span className="text-sm font-semibold tracking-tight text-slate-900">
              EhsimCAD
            </span>
          </Link>

          <div className="flex items-center gap-5">
            <Link
              href="/projects"
              className="text-xs text-slate-500 transition-colors hover:text-slate-900"
            >
              projects
            </Link>
            <Link
              href="/sample"
              className="text-xs text-slate-500 transition-colors hover:text-slate-900"
            >
              sample
            </Link>
            <SignOutButton email={user.email} />
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-8">
        {!verified && (
          <div className="pb-6">
            <VerifyBanner email={user.email} />
          </div>
        )}

        <div className="flex flex-col gap-3 pb-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">Models</h2>
            <p className="pt-1 text-xs text-slate-500">
              {/* The total, not this page's share of it: the heading answers
                  "how many models do I have", and a page of twenty-five
                  cannot. */}
              {total === 0
                ? 'Nothing uploaded yet'
                : `${total} model${total === 1 ? '' : 's'}`}
              {total > PAGE_SIZE && (
                <span className="text-slate-400"> · {page.models.length} shown</span>
              )}
              <span className="text-slate-400"> · {SUPPORTED_FORMAT_NAMES.join(', ')}</span>
            </p>
          </div>

          {/*
            Two entry points rather than one button with a choice inside it.
            The operations promise different things, and a control that treats
            them as two settings of one action says they are the same thing
            differently configured. Different verbs, on purpose: one brings a
            model, the other asks for one to be worked out.
          */}
          {destinations.length === 0 ? (
            <p className="text-xs text-slate-500">
              You have view-only access to the projects you are in, so there is nowhere to
              upload to.
            </p>
          ) : (
            <div className="flex shrink-0 items-center gap-2">
              <Link
                href="/upload"
                className="rounded-md bg-blue-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
              >
                {MODE_NAMES.model}
              </Link>
              <Link
                href="/estimate"
                className="rounded-md border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
              >
                {MODE_NAMES.estimate}
              </Link>
            </div>
          )}
        </div>

        {/* Which project a model is in only means something once there is
            more than one to tell apart. */}
        <ModelList
          models={page.models}
          projects={projects.length > 1 ? projects : []}
          deletable={deletable}
        />

        {/*
          Only where there is somewhere to go. A pair of dead controls under a
          list that fits on one screen is furniture, and this list fits on one
          screen for most of the people who have it.

          Links rather than buttons, because that is what they are: each one
          has an address, and the address is the page. Refreshing keeps your
          place, and so does the poll the list runs while a conversion is in
          flight -- which is the whole reason the cursor lives in the URL
          rather than in component state.
        */}
        {(page.newer || page.older) && (
          <nav className="flex items-center justify-between pt-4 text-xs">
            {page.newer ? (
              <Link
                href={`/?before=${encodeCursor(page.newer)}`}
                className="rounded border border-slate-300 bg-white px-2.5 py-1.5 text-slate-600 transition-colors hover:bg-slate-100"
              >
                ← Newer
              </Link>
            ) : (
              <span />
            )}

            {page.older ? (
              <Link
                href={`/?after=${encodeCursor(page.older)}`}
                className="rounded border border-slate-300 bg-white px-2.5 py-1.5 text-slate-600 transition-colors hover:bg-slate-100"
              >
                Older →
              </Link>
            ) : (
              <span />
            )}
          </nav>
        )}
      </div>
    </main>
  );
}
