/**
 * Which uploads are turned away, and with what explanation.
 *
 * The same function runs in the browser before a file is sent and on the
 * server before a row is written, so the two can never disagree about what is
 * acceptable. The wording is tested too, because it is the useful half: a
 * rejection that does not say what to do instead is barely better than a
 * silent failure, and what to do differs by what the file is.
 */

import { describe, expect, it } from 'vitest';

import { extensionOf, formatOf, rejectionReason } from '@/lib/formats';

describe('rejectionReason', () => {
  it.each(['bracket.step', 'bracket.STEP', 'bracket.stp', 'shaft.iges', 'shaft.igs'])(
    'accepts the B-rep format %s',
    (filename) => {
      expect(rejectionReason(filename)).toBeNull();
    },
  );

  it.each(['mesh.stl', 'mesh.obj', 'mesh.ply', 'scene.glb', 'scene.gltf'])(
    'accepts the mesh format %s',
    (filename) => {
      expect(rejectionReason(filename)).toBeNull();
    },
  );

  describe('native part and assembly files', () => {
    it.each([
      ['bracket.ipt', 'Inventor', 'part'],
      ['frame.iam', 'Inventor', 'assembly'],
      ['bracket.sldprt', 'SolidWorks', 'part'],
      ['frame.sldasm', 'SolidWorks', 'assembly'],
      ['bracket.catpart', 'CATIA', 'part'],
      ['frame.catproduct', 'CATIA', 'assembly'],
    ])('tells the holder of %s to export to STEP', (filename, application, kind) => {
      const reason = rejectionReason(filename) ?? '';

      expect(reason).toContain(application);
      expect(reason).toContain(kind);
      expect(reason).toContain('Export it to STEP');
    });
  });

  describe('drawings', () => {
    it.each([
      ['sheet.idw', 'Inventor'],
      ['sheet.slddrw', 'SolidWorks'],
      ['sheet.catdrawing', 'CATIA'],
    ])('does not tell the holder of %s to export a drawing to STEP', (filename, application) => {
      const reason = rejectionReason(filename) ?? '';

      expect(reason).toContain(application);
      expect(reason).toContain('not a 3D model');
      // A drawing has no solid to export; sending someone to STEP would send
      // them in circles.
      expect(reason).not.toContain('Export it to STEP');
      expect(reason).toContain('the part or assembly it documents');
    });

    it.each(['plan.dxf', 'plan.DXF'])('accepts the drawing %s', (filename) => {
      expect(rejectionReason(filename)).toBeNull();
    });

    it('sends the holder of a DWG to DXF rather than to a modelling application', () => {
      const reason = rejectionReason('plan.dwg') ?? '';

      // Every application that writes DWG writes DXF, so the way out is one
      // menu item away rather than "go and find the model".
      expect(reason).toContain('DXF');
      expect(reason).not.toContain('STEP');
    });

    it('offers DXF to the holder of a native drawing as well', () => {
      const reason = rejectionReason('sheet.slddrw') ?? '';
      expect(reason).toContain('DXF');
    });
  });

  it('gets the article right for each application name', () => {
    expect(rejectionReason('bracket.ipt')).toContain('an Inventor');
    expect(rejectionReason('bracket.sldprt')).toContain('a SolidWorks');
    expect(rejectionReason('bracket.catpart')).toContain('a CATIA');
    expect(rejectionReason('sheet.slddrw')).toContain('a SolidWorks drawing');
  });

  it('handles extensions several applications share without naming one', () => {
    const reason = rejectionReason('housing.prt') ?? '';
    expect(reason).toContain('native CAD');
    expect(reason).toContain('Export it to STEP');
  });

  it('turns away an unrelated file and lists what would work', () => {
    const reason = rejectionReason('notes.pdf') ?? '';
    expect(reason).toContain('.pdf');
    expect(reason).toContain('.step');
  });

  it('turns away a file with no extension at all', () => {
    expect(rejectionReason('README')).not.toBeNull();
  });

  it('is not fooled by a dot inside the name', () => {
    expect(rejectionReason('rev.2.final.step')).toBeNull();
    expect(rejectionReason('rev.2.final.idw')).toContain('not a 3D model');
  });
});

describe('extensionOf and formatOf', () => {
  it('lower-cases the extension', () => {
    expect(extensionOf('Bracket.STEP')).toBe('.step');
    expect(formatOf('Bracket.STEP')).toBe('step');
  });

  it('reads only the last extension', () => {
    expect(extensionOf('archive.tar.gz')).toBe('.gz');
  });

  it('returns nothing for a name without one', () => {
    expect(extensionOf('README')).toBe('');
    expect(formatOf('README')).toBe('');
  });
});
