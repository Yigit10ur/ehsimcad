/**
 * What a drop is allowed to carry, and what it is told when it carries more.
 *
 * A file picker asks for one file of a known kind and gets one. A drop is
 * whatever the operating system will let go of over the window, so these are
 * the four things that can arrive and the three that have to be turned back.
 * The wording is tested with them: every refusal here is a person standing
 * over a drop zone that did nothing, and the only useful answer says what to
 * drop instead.
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
     * among the files, it has a name, and reading it yields nothing. Every
     * test after this one would have accepted it.
     */
    const reason = refusalFor(drop([file('parts')], [folder]));

    expect(reason).toContain('folder');
    expect(reason).toContain('drag the file itself');
  });

  it('says folder even when a real file came with it', () => {
    const reason = refusalFor(drop([file('parts'), file('bracket.step')], [folder, { kind: 'file' }]));
    expect(reason).toContain('folder');
  });

  it('turns back several files, and says how many', () => {
    const reason = refusalFor(drop([file('a.step'), file('b.step'), file('c.step')]));

    // Counting them is what tells someone the drop was seen. "One at a time"
    // alone reads like a refusal of the file rather than of the number.
    expect(reason).toContain('3');
    expect(reason).toContain('one');
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
      refusalFor(drop([file('a.step'), file('b.step')])),
      refusalFor({ files: [], items: [{ kind: 'string' }] }),
    ];

    for (const reason of refusals) {
      expect(reason).toMatch(/Drag|Drop|Open/);
      expect(reason.endsWith('.')).toBe(true);
    }
  });
});
