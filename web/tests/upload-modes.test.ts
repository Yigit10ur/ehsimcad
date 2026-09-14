/**
 * The two modes, and what keeps them apart.
 *
 * The platform does two different things to an upload: it opens a model, or it
 * estimates one from a drawing. They are separate operations in the interface
 * because they promise different amounts, and these are the rules that keep
 * that separation from being decoration -- which files each mode takes, what a
 * file offered to the wrong one is told, and which mode a model that already
 * exists belongs to.
 */

import { describe, expect, it } from 'vitest';

import {
  MODE_NAMES,
  SUPPORTED_EXTENSIONS,
  SUPPORTED_FORMATS,
  extensionsFor,
  formatNamesFor,
  lengthNeeded,
  modeForFile,
  rejectionReason,
  type UploadMode,
} from '@/lib/formats';
import { modeOf } from '@/lib/models';

const MODES: UploadMode[] = ['model', 'estimate'];

describe('every format belongs to exactly one mode', () => {
  it('leaves no extension in neither mode', () => {
    // An extension the catalogue accepts but no mode offers would be reachable
    // by the converter and unreachable by a person.
    for (const extension of SUPPORTED_EXTENSIONS) {
      expect(modeForFile(`part${extension}`)).not.toBeNull();
    }
  });

  it('puts no extension in both modes', () => {
    const model = new Set(extensionsFor('model'));
    const overlap = extensionsFor('estimate').filter((extension) => model.has(extension));
    expect(overlap).toEqual([]);
  });

  it('accounts for every supported extension between the two', () => {
    const covered = [...extensionsFor('model'), ...extensionsFor('estimate')];
    expect(new Set(covered)).toEqual(new Set(SUPPORTED_EXTENSIONS));
  });

  it('names each mode something a person can be told to press', () => {
    for (const mode of MODES) {
      expect(MODE_NAMES[mode].length).toBeGreaterThan(0);
      expect(formatNamesFor(mode).length).toBeGreaterThan(0);
    }
  });
});

describe('which mode a file belongs to', () => {
  it.each(['bracket.step', 'bracket.stp', 'shaft.iges', 'mesh.stl', 'scene.glb'])(
    'reads %s as something to open',
    (filename) => {
      expect(modeForFile(filename)).toBe('model');
    },
  );

  it.each(['shaft.dxf', 'shaft.pdf', 'scan.png', 'scan.jpg', 'scan.tiff'])(
    'reads %s as something to estimate from',
    (filename) => {
      expect(modeForFile(filename)).toBe('estimate');
    },
  );

  it('is not fooled by capitals or by a dot inside the name', () => {
    expect(modeForFile('Bracket.STEP')).toBe('model');
    expect(modeForFile('rev.2.final.DXF')).toBe('estimate');
  });

  it('claims nothing for a file neither mode accepts', () => {
    expect(modeForFile('notes.zip')).toBeNull();
    expect(modeForFile('bracket.sldprt')).toBeNull();
    expect(modeForFile('README')).toBeNull();
  });
});

describe('a file offered to the wrong mode', () => {
  it('turns a drawing away from the model mode, and names the other one', () => {
    const reason = rejectionReason('shaft.dxf', 'model') ?? '';

    expect(reason).toContain('.dxf');
    expect(reason).toContain(MODE_NAMES.estimate);
    // The file is fine. Saying it is unsupported would send someone looking for
    // a different file rather than a different button.
    expect(reason).not.toContain('is not supported');
  });

  it('turns a model away from the estimate mode, and names the other one', () => {
    const reason = rejectionReason('bracket.step', 'estimate') ?? '';

    expect(reason).toContain('.step');
    expect(reason).toContain(MODE_NAMES.model);
    expect(reason).not.toContain('is not supported');
  });

  it('accepts every file in the mode it belongs to', () => {
    for (const extension of SUPPORTED_EXTENSIONS) {
      const mode = modeForFile(`part${extension}`);
      expect(mode).not.toBeNull();
      expect(rejectionReason(`part${extension}`, mode as UploadMode)).toBeNull();
    }
  });

  it('refuses every file in the mode it does not belong to', () => {
    for (const extension of SUPPORTED_EXTENSIONS) {
      const other = modeForFile(`part${extension}`) === 'model' ? 'estimate' : 'model';
      expect(rejectionReason(`part${extension}`, other)).not.toBeNull();
    }
  });

  it('still answers the old question when no mode is given', () => {
    // The converter's own dispatch asks only whether the platform can read the
    // file at all, and that answer must not have changed.
    for (const extension of SUPPORTED_EXTENSIONS) {
      expect(rejectionReason(`part${extension}`)).toBeNull();
    }
  });
});

describe('a file neither mode accepts', () => {
  it('offers only the extensions of the mode being used', () => {
    const inModelMode = rejectionReason('notes.zip', 'model') ?? '';
    expect(inModelMode).toContain('.step');
    // Listing the other mode's formats here invites a second refusal.
    expect(inModelMode).not.toContain('.dxf');

    const inEstimateMode = rejectionReason('notes.zip', 'estimate') ?? '';
    expect(inEstimateMode).toContain('.dxf');
    expect(inEstimateMode).not.toContain('.step');
  });

  it('keeps the advice that has somewhere better to send people', () => {
    // A native CAD file is refused for its own reason in either mode, and that
    // reason is more useful than a list of extensions.
    expect(rejectionReason('bracket.sldprt', 'model')).toContain('Export it to STEP');
    expect(rejectionReason('plan.dwg', 'estimate')).toContain('DXF');
  });
});

describe('being asked how long the part is', () => {
  it('is only ever asked in the estimate mode', () => {
    /*
     * A length rescales what is read off a sheet. Asking for one about a file
     * that states its own units would let a typed number quietly resize a
     * model that was already right, so the question belongs to one mode only.
     */
    for (const extension of SUPPORTED_EXTENSIONS) {
      if (lengthNeeded(`part${extension}`) !== 'none') {
        expect(modeForFile(`part${extension}`)).toBe('estimate');
      }
    }
  });

  it('is never asked about a format the model mode offers', () => {
    for (const extension of extensionsFor('model')) {
      expect(lengthNeeded(`part${extension}`)).toBe('none');
    }
  });
});

describe('which mode a model that already exists belongs to', () => {
  /*
   * Read from the first version, like the uploader is: the first upload is
   * what created the model, and a revision revises what that upload made. A
   * model is either something that was opened or something that was estimated,
   * and no later version changes which.
   */
  it('takes it from the first version, whatever order they arrive in', () => {
    const versions = [
      { versionNo: 3, mode: 'model' as const },
      { versionNo: 1, mode: 'estimate' as const },
      { versionNo: 2, mode: 'model' as const },
    ];

    expect(modeOf(versions)).toBe('estimate');
  });

  it('answers for a model with a single version', () => {
    expect(modeOf([{ versionNo: 1, mode: 'estimate' }])).toBe('estimate');
    expect(modeOf([{ versionNo: 1, mode: 'model' }])).toBe('model');
  });

  it('does not call a model an estimate when it knows of no versions', () => {
    // Nothing renders this, but guessing `estimate` would put the louder claim
    // on the thinner evidence.
    expect(modeOf([])).toBe('model');
  });
});

describe('the format list itself', () => {
  it('tags every entry with a mode', () => {
    for (const format of SUPPORTED_FORMATS) {
      expect(MODES).toContain(format.mode);
    }
  });
});
