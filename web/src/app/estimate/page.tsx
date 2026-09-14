import Link from 'next/link';
import { redirect } from 'next/navigation';

import { SignOutButton } from '@/components/auth/SignOutButton';
import { UploadForm } from '@/components/catalogue/UploadForm';
import { MODE_NAMES } from '@/lib/formats';
import { currentUser, personalProject, writableProjects } from '@/lib/session';

export const dynamic = 'force-dynamic';

/**
 * The second of the two modes: a part worked out from a drawing of it.
 *
 * Everything above the file picker is the reason this is a page rather than a
 * second button. What this can read is narrow, what it produces is a guess,
 * and both have to be said before a file is chosen -- afterwards is too late
 * to be a choice. The refusals are listed next to the acceptances for the same
 * reason: somebody holding a drawing of a milled bracket should find that out
 * here, not from a plausible cylinder.
 */
export default async function EstimatePage() {
  const user = await currentUser();
  if (!user) redirect('/sign-in');

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
          {MODE_NAMES.estimate}
        </h1>
        <p className="pt-2 text-sm leading-relaxed text-slate-600">
          A drawing documents a part without containing one. This reads the outline and the
          centre line off the sheet and revolves one about the other, which produces a real
          solid — and a guess about what the sheet meant.
        </p>

        <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-xs font-medium text-amber-900">
            The result is an estimate, and stays labelled as one.
          </p>
          <p className="pt-1 text-xs leading-relaxed text-amber-800">
            It measures as exactly as any other model. What is uncertain is not the numbers
            but whether it is the right shape, so the viewer shows what the reading assumed
            and what it ignored.
          </p>
        </div>

        <div className="grid gap-4 pt-6 sm:grid-cols-2">
          <div>
            <h2 className="text-xs font-medium text-slate-900">What it can read</h2>
            <ul className="space-y-1 pt-1.5 text-xs leading-relaxed text-slate-600">
              <li>Turned parts: a profile revolved about a centre line</li>
              <li>Shafts, bushes, pins, spacers, flanged and stepped bodies</li>
              <li>Fillets and chamfers, kept as arcs rather than flattened</li>
            </ul>
          </div>

          <div>
            <h2 className="text-xs font-medium text-slate-900">What it cannot</h2>
            <ul className="space-y-1 pt-1.5 text-xs leading-relaxed text-slate-600">
              <li>Milled and prismatic parts, and anything drawn in several views</li>
              <li>Cross holes, pockets, keyways and threads</li>
              <li>A sheet with no centre line, which is what tells it the axis</li>
            </ul>
          </div>
        </div>

        <div className="pt-7">
          <UploadForm destinations={destinations} mode="estimate" />
        </div>

        <div className="pt-6 text-xs leading-relaxed text-slate-500">
          <p>
            <span className="font-medium text-slate-700">DXF</span> reads best: dimensions,
            notes and the title block are separate kinds of entity there, so telling the part
            from the annotation needs no guessing.{' '}
            <span className="font-medium text-slate-700">PDF</span> keeps the geometry but
            loses those names.{' '}
            <span className="font-medium text-slate-700">A photograph or scan</span> keeps
            only dark pixels, and has to be told how long the part is.
          </p>
        </div>

        <p className="pt-6 text-xs text-slate-500">
          Holding the model itself rather than a drawing of it?{' '}
          <Link href="/upload" className="text-blue-600 hover:underline">
            {MODE_NAMES.model}
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
