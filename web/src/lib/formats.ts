/**
 * Which uploads the converter can do something with, and why the rest cannot.
 *
 * The same function runs in the browser before a file is sent and on the
 * server before a row is written, so the two can never disagree. Rejecting
 * early matters: the alternative is accepting the file, queueing it, and
 * failing twenty minutes later with a message nobody reads.
 *
 * The wording is part of the behaviour. Someone holding a native CAD file
 * needs to be told what to do instead, and that answer is different for a part
 * than it is for a drawing.
 *
 * Every format also belongs to one of two modes, because the platform does two
 * different things and they promise different amounts. Opening a model shows
 * what the file already contains. Estimating from a drawing reconstructs
 * something the file never contained, and can be wrong about it. Which one is
 * happening is chosen before the file is, so that nobody learns which promise
 * they were given by reading the result.
 */

/**
 * The two things a person can ask of an upload.
 *
 * `model` opens a file that already contains a solid, and reports what is in
 * it. `estimate` reads a 2D drawing and reconstructs a part the drawing only
 * documents -- a guess, labelled as one everywhere it surfaces.
 *
 * The distinction is not a technical one: the extensions of the two modes do
 * not overlap, so the file alone would settle it. It is kept because the two
 * promise different things, and which promise was made should be visible
 * before the file is chosen rather than inferred from the result afterwards.
 */
export type UploadMode = 'model' | 'estimate';

/** What each mode is called wherever a person reads about it. */
export const MODE_NAMES: Record<UploadMode, string> = {
  model: 'Upload a model',
  estimate: 'Estimate from a drawing',
};

/**
 * What is accepted, by the name a person would use for it.
 *
 * The catalogue names the formats and the rejection lists the extensions, and
 * they used to be two lists: one was updated for DXF and the other was not, so
 * the page said one thing and the error said another. Both are read from here
 * now, which makes them impossible to disagree rather than merely tested.
 */
export const SUPPORTED_FORMATS = [
  { name: 'STEP', mode: 'model', extensions: ['.step', '.stp'] },
  { name: 'IGES', mode: 'model', extensions: ['.iges', '.igs'] },
  { name: 'STL', mode: 'model', extensions: ['.stl'] },
  { name: 'OBJ', mode: 'model', extensions: ['.obj'] },
  { name: 'PLY', mode: 'model', extensions: ['.ply'] },
  { name: 'glTF', mode: 'model', extensions: ['.glb', '.gltf'] },
  // A drawing rather than a model, and read as one: a turned part is
  // reconstructed from its profile and centre line, and labelled `derived` so
  // that nothing it produces is taken for a part somebody modelled. See
  // ARCHITECTURE.md section 12.
  { name: 'DXF', mode: 'estimate', extensions: ['.dxf'] },
  // The same drawing after it was printed. It keeps the geometry -- a line is
  // still a line, with coordinates -- and loses only the names for things.
  { name: 'PDF', mode: 'estimate', extensions: ['.pdf'] },
  // And a picture of one, which keeps nothing but dark pixels. Named as a
  // group: nobody chooses between PNG and TIFF, they upload what they have.
  {
    name: 'images',
    mode: 'estimate',
    extensions: ['.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'],
  },
] as const;

/**
 * Whether a file needs to be told how long the part is, and how badly.
 *
 * A DXF has real coordinates and is never asked. A printed sheet is drawn at
 * whatever scale it was plotted at, so a length settles it -- but a sheet at
 * least has a paper size, so it can be read without one and say what it
 * assumed. A picture has nothing: the same image is a bolt or a bridge, and
 * there is no reading of it at all without being told which.
 */
export type LengthNeed = 'required' | 'optional' | 'none';

const IMAGE_EXTENSIONS: readonly string[] =
  SUPPORTED_FORMATS.find((format) => format.name === 'images')?.extensions ?? [];

export function lengthNeeded(filename: string): LengthNeed {
  const extension = extensionOf(filename);
  if (IMAGE_EXTENSIONS.includes(extension)) return 'required';
  if (extension === '.pdf') return 'optional';
  return 'none';
}

export const SUPPORTED_EXTENSIONS: readonly string[] = SUPPORTED_FORMATS.flatMap(
  (format) => format.extensions,
);

/** For the line under the catalogue heading. */
export const SUPPORTED_FORMAT_NAMES: readonly string[] = SUPPORTED_FORMATS.map(
  (format) => format.name,
);

/** The formats one mode accepts, in the order they are listed above. */
export function formatsFor(mode: UploadMode) {
  return SUPPORTED_FORMATS.filter((format) => format.mode === mode);
}

/**
 * What to put in a file picker's `accept`.
 *
 * Narrowing the picker is half of what makes the modes separate: the other
 * mode's files are not offered, so choosing the wrong one takes deliberate
 * effort rather than being the default.
 */
export function extensionsFor(mode: UploadMode): readonly string[] {
  return formatsFor(mode).flatMap((format) => [...format.extensions]);
}

/** For the line under a mode's heading. */
export function formatNamesFor(mode: UploadMode): readonly string[] {
  return formatsFor(mode).map((format) => format.name);
}

/**
 * Which mode a file belongs to, or null if it belongs to neither.
 *
 * Used to catch a file chosen in the wrong mode, and to say which mode it
 * should have gone to. Never used to pick the mode on the uploader's behalf:
 * silently doing the other operation is exactly what having two modes is meant
 * to stop.
 */
export function modeForFile(filename: string): UploadMode | null {
  const extension = extensionOf(filename);
  const format = SUPPORTED_FORMATS.find((candidate) =>
    (candidate.extensions as readonly string[]).includes(extension),
  );
  return format?.mode ?? null;
}

type NativeKind = 'part' | 'assembly' | 'drawing';

/** Used where several applications share an extension and none can be named. */
const UNNAMED = 'a CAD';

interface NativeFormat {
  kind: NativeKind;
  application: string;
}

/**
 * Native formats, by the application that writes them.
 *
 * None of these can be read without a commercial SDK (ARCHITECTURE.md
 * section 9). Grouping them by kind rather than listing them flat is what
 * lets the message be useful: a part and an assembly both export to STEP, a
 * drawing does not export to anything this platform can show.
 */
const NATIVE_FORMATS: Record<string, NativeFormat> = {
  // Autodesk Inventor
  '.ipt': { kind: 'part', application: 'Inventor' },
  '.iam': { kind: 'assembly', application: 'Inventor' },
  '.idw': { kind: 'drawing', application: 'Inventor' },

  // SolidWorks
  '.sldprt': { kind: 'part', application: 'SolidWorks' },
  '.sldasm': { kind: 'assembly', application: 'SolidWorks' },
  '.slddrw': { kind: 'drawing', application: 'SolidWorks' },

  // CATIA
  '.catpart': { kind: 'part', application: 'CATIA' },
  '.catproduct': { kind: 'assembly', application: 'CATIA' },
  '.catdrawing': { kind: 'drawing', application: 'CATIA' },

  // Creo and Solid Edge share these, so the application is left unnamed.
  '.prt': { kind: 'part', application: 'a CAD' },
  '.asm': { kind: 'assembly', application: 'a CAD' },
  '.par': { kind: 'part', application: 'Solid Edge' },
  '.psm': { kind: 'part', application: 'Solid Edge' },
  '.dft': { kind: 'drawing', application: 'Solid Edge' },
  '.drw': { kind: 'drawing', application: 'a CAD' },
};

/**
 * The drawing format that cannot be read, next to the one that can.
 *
 * DWG is AutoCAD's own and needs a licensed library; DXF is the interchange
 * format it exports, and every application that writes one writes the other.
 * So the answer is not "upload the model instead" -- it is one menu item away.
 */
const DRAWING_EXCHANGE = ['.dwg'];

/**
 * Picture formats that cannot be opened, next to the ones that can.
 *
 * Somebody holding one of these is holding a drawing and has somewhere useful
 * to go, and it is one Save As away rather than back to the CAD system.
 */
const PICTURES = ['.heic', '.heif', '.avif', '.gif'];

export function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot === -1 ? '' : filename.slice(dot).toLowerCase();
}

export function formatOf(filename: string): string {
  return extensionOf(filename).replace('.', '');
}

/** "an Inventor", but "a SolidWorks" and "a CATIA". */
function withArticle(noun: string): string {
  return `${/^[aeiou]/i.test(noun) ? 'an' : 'a'} ${noun}`;
}

function nativeMessage(extension: string, format: NativeFormat): string {
  const named = format.application !== UNNAMED;
  const source = named ? withArticle(format.application) : 'a native CAD';

  /*
   * Which application does the exporting, said out loud.
   *
   * Whoever reads this is quite likely the one without a seat -- serving the
   * machines that have no CAD licence is what this platform is for. "Export it
   * to STEP" is then an instruction they cannot carry out, and naming the
   * application is what turns it into one they can pass on to somebody who
   * can.
   *
   * Left off where several applications share an extension and none can be
   * named: "export it from a CAD" tells nobody anything.
   */
  const from = named ? ` from ${format.application}` : '';

  if (format.kind === 'drawing') {
    // Telling someone to export a drawing to STEP would send them in circles:
    // a drawing has no solid to export. What they want is the model it
    // documents.
    const owner = named ? withArticle(format.application) : 'a';
    return `${extension} is ${owner} drawing, not a 3D model. Upload the part or assembly it documents, exported to STEP${from} — or, for a turned part, save the drawing as DXF.`;
  }

  return `${extension} is ${source} ${format.kind} file, which needs a commercial SDK to read. Export it to STEP${from} and upload that.`;
}

/**
 * A file that is accepted, but not by the mode it was offered to.
 *
 * Worth its own sentence rather than the generic list: the person is holding
 * something this platform can read, and the only thing wrong is which of the
 * two operations they started. Naming the other one is the whole answer.
 */
function wrongModeMessage(extension: string, chosen: UploadMode): string {
  if (chosen === 'estimate') {
    return `${extension} is a 3D model, not a drawing — there is nothing in it to estimate. Open it with “${MODE_NAMES.model}”, which reports the geometry the file already contains.`;
  }

  return `${extension} is a drawing, not a 3D model. Read it with “${MODE_NAMES.estimate}”, which reconstructs a turned part from it and says what it assumed.`;
}

/**
 * Why this file cannot be uploaded, or null if it can.
 *
 * `mode` is which operation is being attempted. Left out, the question is only
 * "can this platform read it at all", which is what the converter's own
 * dispatch asks. Passed, a file belonging to the other mode is turned away
 * too -- accepting it would mean quietly doing the operation the uploader did
 * not choose.
 */
export function rejectionReason(filename: string, mode?: UploadMode): string | null {
  const extension = extensionOf(filename);

  if (SUPPORTED_EXTENSIONS.includes(extension)) {
    if (!mode || modeForFile(filename) === mode) return null;
    return wrongModeMessage(extension, mode);
  }

  const native = NATIVE_FORMATS[extension];
  if (native) return nativeMessage(extension, native);

  if (PICTURES.includes(extension)) {
    return `${extension} is a picture this cannot open. Save it as PNG or JPEG — or, better, save the drawing itself as DXF.`;
  }

  if (DRAWING_EXCHANGE.includes(extension)) {
    return `${extension} needs a licensed library to read. Save the same drawing as DXF, which every application that writes ${extension} can also write.`;
  }

  // Listing the other mode's extensions here would be an invitation to try
  // them in a mode that will refuse them.
  const offered = mode ? extensionsFor(mode) : SUPPORTED_EXTENSIONS;
  return `${extension || 'that file type'} is not supported. Upload one of: ${offered.join(', ')}.`;
}
