/**
 * The one file a drop meant, out of whatever it was carrying.
 *
 * A file picker can only return files, and only as many as it was told to
 * accept. A drop can carry anything the operating system will let go of: a
 * folder, a selection of eleven parts, a link dragged out of another tab, a
 * piece of text.
 *
 * The upload takes one file, and takes it without comment -- the first real
 * file in the drop goes up and the zone closes behind it, rather than the
 * whole drop being refused over its size. What is left are the two drops that
 * carry no file to take at all, and those are said out loud: a drop zone gives
 * no other sign, and "nothing happened" is what a person sees otherwise.
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

/**
 * The dropped files, with any folder among them left behind.
 *
 * `files` holds one entry per item of kind `file`, in the same order, so the
 * items that are folders are the entries to drop. Anything that does not line
 * up -- a browser that lists no items at all -- is left as it came: a folder
 * that cannot be recognised is better uploaded than a file that is quietly
 * discarded for being mistaken for one.
 */
function withoutFolders(files: File[], items: DroppedItem[]): File[] {
  const asFiles = items.filter((item) => item.kind === 'file');
  if (asFiles.length !== files.length) return files;

  return files.filter((_, index) => !isFolder(asFiles[index]));
}

export function fileFromDrop(contents: DropContents | null | undefined): Drop {
  const items = contents?.items ? Array.from(contents.items) : [];
  const files = contents?.files ? Array.from(contents.files) : [];
  const usable = withoutFolders(files, items);

  if (usable.length > 0) return { file: usable[0] };

  if (files.length > 0) {
    // Everything dropped was a folder. The failure that looks most like a
    // success: a folder is counted among the files, it has a name, and reading
    // it yields nothing at all.
    return { error: 'That is a folder. Open it and drag the file itself.' };
  }

  // A link or a piece of selected text dragged out of another page. It has a
  // name and an icon and is not a file, and saying "no file" alone would read
  // as a fault in the page rather than in what was dragged.
  return { error: 'That was not a file. Drag one in from a folder rather than from a page.' };
}
