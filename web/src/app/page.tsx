import { desc, inArray } from 'drizzle-orm';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { ModelList, type ModelWithVersions } from '@/components/catalogue/ModelList';
import { UploadForm } from '@/components/catalogue/UploadForm';
import { SignOutButton } from '@/components/auth/SignOutButton';
import { VerifyBanner } from '@/components/auth/VerifyBanner';
import { db, schema } from '@/db';
import { SUPPORTED_FORMAT_NAMES } from '@/lib/formats';
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
async function loadModels(userId: string): Promise<ModelWithVersions[]> {
  // Make sure the user has somewhere to upload to, then list everything they
  // can read -- not just that one project, or a model shared with them would
  // be openable by URL but invisible in the catalogue.
  await personalProject(userId);
  const projectIds = await readableProjects(userId);

  return db.query.models.findMany({
    where: inArray(schema.models.projectId, projectIds),
    orderBy: [desc(schema.models.createdAt)],
    with: { versions: { orderBy: [desc(schema.modelVersions.versionNo)] } },
  });
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

export default async function Home() {
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

  let models: ModelWithVersions[];
  let projects: Awaited<ReturnType<typeof projectsFor>>;
  let destinations: { id: string; name: string }[];
  let deletable: string[];
  let verified = true;
  try {
    models = await loadModels(user.id);
    deletable = [...(await deletableIds(models, user.id))];
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
              {models.length === 0
                ? 'Nothing uploaded yet'
                : `${models.length} model${models.length === 1 ? '' : 's'}`}
              <span className="text-slate-400"> · {SUPPORTED_FORMAT_NAMES.join(', ')}</span>
            </p>
          </div>

          <UploadForm destinations={destinations} />
        </div>

        {/* Which project a model is in only means something once there is
            more than one to tell apart. */}
        <ModelList
          models={models}
          projects={projects.length > 1 ? projects : []}
          deletable={deletable}
        />
      </div>
    </main>
  );
}
