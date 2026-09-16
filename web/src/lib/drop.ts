/**
 * What was let go over the drop zone, and whether it is one file.
 *
 * A file picker can only return files, and only as many as it was told to
 * accept. A drop can carry anything the operating system will let go of: a
 * folder, a selection of eleven parts, a link dragged out of another tab, a
 * piece of text. The upload takes one file, so the rest have to be turned away
 * -- and turned away by name, because "nothing happened" is what a person sees
 * otherwise, and the reason it did not happen is different every time.
 *
 * Kept apart from the form so that it can be read without a browser. The
 * shapes below are the parts of `DataTransfer` this needs, which is what lets
 * a test describe a drop without staging one.
 */

/** As much of a `DataTransferItem` as it takes to tell a folder from a file. */
export interface DroppedItem {
  kind: string;
  webkitGetAsEntry?: () => { isDirectory: boolean } | null;
}

/** As much of a `DataTransfer` as a drop is read from. */
export interface DropContents {
  files: ArrayLike<File>;
  /*
   * Absent in older browsers, and the only place a folder announces itself:
   * a dropped folder arrives in `files` looking like a file with no type, and
   * uploading it produces an empty body rather than an error.
   */
  items?: ArrayLike<DroppedItem> | null;
}

/**
 * Either the one file to upload, or what to tell whoever dropped it.
 *
 * Two shapes rather than one with two nullable halves, so that there is no
 * fourth case to write code for: no drop can arrive carrying both a file and a
 * refusal, or neither.
 */
export type Drop = { file: File } | { error: string };

function isFolder(item: DroppedItem): boolean {
  return item.kind === 'file' && item.webkitGetAsEntry?.()?.isDirectory === true;
}

function refuse(error: string): Drop {
  return { error };
}

/**
 * The one file a drop meant, or the reason it did not carry one.
 *
 * The folder check comes first because a folder is the failure that looks most
 * like a success: it is counted as a file, it has a name, and it would be
 * accepted by every test below it.
 */
export function fileFromDrop(contents: DropContents | null | undefined): Drop {
  const items = contents?.items ? Array.from(contents.items) : [];
  const files = contents?.files ? Array.from(contents.files) : [];

  if (items.some(isFolder)) {
    return refuse('That is a folder. Open it and drag the file itself.');
  }

  if (files.length === 0) {
    // A link or a piece of selected text dragged out of another page. It has a
    // name and an icon and is not a file, and saying "no file" alone would
    // read as a fault in the page rather than in what was dragged.
    return refuse('That was not a file. Drag one in from a folder rather than from a page.');
  }

  if (files.length > 1) {
    return refuse(
      `${files.length} files at once, and this uploads one. Drop the one to start with.`,
    );
  }

  return { file: files[0] };
}
