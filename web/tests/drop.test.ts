/**
 * Which file a drop meant, out of whatever it was carrying.
 *
 * A file picker asks for one file of a known kind and gets one. A drop is
 * whatever the operating system will let go of over the window, so these are
 * the things that can arrive and what is taken from each. A drop of several is
 * one upload rather than a refusal; only a drop with no file in it at all has
 * nothing to take, and those two say so. The wording is tested with them: a
 * drop zone gives no other sign, and a reason that only describes the mistake
 * leaves the same dead end as silence.
 */

import { describe, expect, it } from 'vitest';

import { fileFromDrop, type DropContents, type DroppedItem } from '@/lib/drop';

function file(name: string): File {
  return new File(['solid'], name, { type: 'application/octet-stream' });
}

/** A `DataTransfer` as the browser hands one over, without the browser. */
function drop(files: File[], items?: DroppedItem[] | null): DropContents {
  return {
    files,
    items: items ?? files.map(() => ({ kind: 'file', webkitGetAsEntry: () => null })),
  };
}

/** The folder half of a `DataTransferItem`, which is what gives it away. */
const folder: DroppedItem = {
  kind: 'file',
  webkitGetAsEntry: () => ({ isDirectory: true }),
};

function refusalFor(contents: DropContents | null): string {
  const result = fileFromDrop(contents);
  expect('error' in result).toBe(true);
  return 'error' in result ? result.error : '';
}

describe('the one file a drop meant', () => {
  it('takes a single file', () => {
    const dropped = file('bracket.step');
    expect(fileFromDrop(drop([dropped]))).toEqual({ file: dropped });
  });

  it('takes it from a browser that lists no items', () => {
    // `items` is the newer half of the interface. Without it a folder cannot
    // be told from a file, but a file is still a file, and refusing every
    // drop because the browser is old refuses the ordinary case too.
    const dropped = file('shaft.dxf');
    expect(fileFromDrop({ files: [dropped] })).toEqual({ file: dropped });
    expect(fileFromDrop({ files: [dropped], items: null })).toEqual({ file: dropped });
  });

  it('takes it from an item that cannot say whether it is a folder', () => {
    const dropped = file('shaft.dxf');
    const result = fileFromDrop(drop([dropped], [{ kind: 'file' }]));
    expect(result).toEqual({ file: dropped });
  });

  it('takes the first of several rather than refusing them all', () => {
    /*
     * The form uploads one and closes behind it, so the other four are left
     * where they were. Refusing the drop over its size would leave the person
     * with nothing uploaded and a count they did not need; taking one leaves
     * them with the part they dropped first and four still to hand.
     */
    const first = file('a.step');
    const result = fileFromDrop(drop([first, file('b.step'), file('c.step')]));

    expect(result).toEqual({ file: first });
  });

  it('leaves a folder behind and takes the file beside it', () => {
    const wanted = file('bracket.step');
    const result = fileFromDrop(drop([file('parts'), wanted], [folder, { kind: 'file' }]));

    expect(result).toEqual({ file: wanted });
  });

  it('takes the file from a drop that also carries a name for it', () => {
    // An image dragged out of another page arrives as a string item and a
    // file item together. Only the file items line up with `files`, so the
    // string must not shift which one is read as a folder.
    const wanted = file('sheet.png');
    const contents = { files: [wanted], items: [{ kind: 'string' }, { kind: 'file' }] };

    expect(fileFromDrop(contents)).toEqual({ file: wanted });
  });

  it('does not judge what the file is, which the mode does', () => {
    // Anything with a name gets through here. Whether this mode takes a .zip
    // is `rejectionReason`'s answer, and asking it twice in two places is how
    // the picker and the drop zone would come to disagree.
    expect(fileFromDrop(drop([file('notes.zip')]))).toHaveProperty('file');
  });
});

describe('what a drop carries instead', () => {
  it('turns back a folder, which otherwise uploads as an empty file', () => {
    /*
     * The failure that looks most like a success: a dropped folder is counted
     * among the files, it has a name, and reading it yields nothing.
     */
    const reason = refusalFor(drop([file('parts')], [folder]));

    expect(reason).toContain('folder');
    expect(reason).toContain('drag the file itself');
  });

  it('turns back a drop of nothing but folders', () => {
    const reason = refusalFor(drop([file('parts'), file('fixtures')], [folder, folder]));
    expect(reason).toContain('folder');
  });

  it('turns back a link or a selection dragged out of another page', () => {
    // These arrive as items with nothing behind them: a name, an icon, and no
    // file. Saying only "no file" would read as a fault in this page.
    const reason = refusalFor({ files: [], items: [{ kind: 'string' }] });

    expect(reason).toContain('not a file');
    expect(reason).toContain('folder');
  });

  it('turns back an empty drop rather than throwing', () => {
    expect(refusalFor({ files: [] })).not.toHaveLength(0);
    expect(refusalFor(null)).not.toHaveLength(0);
  });

  it('tells every refusal what to do next', () => {
    // A drop zone gives no other feedback: nothing moves, nothing uploads. A
    // reason that only describes the mistake leaves the same dead end.
    const refusals = [
      refusalFor(drop([file('parts')], [folder])),
      refusalFor({ files: [], items: [{ kind: 'string' }] }),
    ];

    for (const reason of refusals) {
      expect(reason).toMatch(/Drag|Drop|Open/);
      expect(reason.endsWith('.')).toBe(true);
    }
  });
});
