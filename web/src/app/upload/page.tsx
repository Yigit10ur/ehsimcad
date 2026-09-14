import Link from 'next/link';
import { redirect } from 'next/navigation';

import { SignOutButton } from '@/components/auth/SignOutButton';
import { UploadForm } from '@/components/catalogue/UploadForm';
import { MODE_NAMES, formatNamesFor } from '@/lib/formats';
import { currentUser, personalProject, writableProjects } from '@/lib/session';

export const dynamic = 'force-dynamic';

/**
 * The first of the two modes: a file that already contains a solid.
 *
 * Almost empty on purpose. There is nothing to warn anybody about here --
 * what comes out is what the file contains -- and a page that looks simple
 * next to one that asks for conditions is itself telling the truth about the
 * difference between them.
 */
export default async function UploadPage() {
  const user = await currentUser();
  if (!user) redirect('/sign-in');

  // Same as the catalogue: make sure there is somewhere to upload to before
  // asking for a file, or arriving here directly would offer no destination.
  await personalProject(user.id);
  const destinations = (await writableProjects(user.id)).map((project) => ({
    id: project.id,
    name: project.name,
  }));

  return (
    <main className="min-h-dvh bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-6 py-4">
          <Link href="/" className="text-sm text-blue-600 hover:underline">
            ← Models
          </Link>
          <SignOutButton email={user.email} />
        </div>
      </header>

      <div className="mx-auto max-w-2xl px-6 py-10">
        <h1 className="text-lg font-semibold tracking-tight text-slate-900">
          {MODE_NAMES.model}
        </h1>
        <p className="pt-2 text-sm leading-relaxed text-slate-600">
          Opens a file that already holds the geometry, and reports what is in it. Volume,
          surface area, centre of mass and every measurement come from the solid the file
          defines, not from the triangles drawn on screen.
        </p>

        <div className="pt-6">
          <UploadForm destinations={destinations} mode="model" />
        </div>

        <p className="pt-6 text-xs text-slate-500">
          {formatNamesFor('model').join(', ')}. A mesh format carries no solid, so its
          properties are labelled as measured rather than exact.
        </p>

        <p className="pt-6 text-xs text-slate-500">
          Holding a drawing rather than a model?{' '}
          <Link href="/estimate" className="text-blue-600 hover:underline">
            {MODE_NAMES.estimate}
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
