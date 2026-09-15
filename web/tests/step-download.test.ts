/**
 * What an estimated STEP file is called once it leaves the platform.
 *
 * The name is one of three places the warning lives -- the others are the
 * product name inside the file and its header -- and it is the only one
 * visible without opening anything. It is also the one that has to survive a
 * Content-Disposition header, which is ASCII and takes quotes as structure
 * rather than as text.
 */

import { describe, expect, it } from 'vitest';

import { estimatedStepFilename } from '@/lib/storage';

describe('estimatedStepFilename', () => {
  it('says it is an estimate, in the name', () => {
    // Before anyone opens the file, and after they have forwarded it.
    expect(estimatedStepFilename('stepped_shaft')).toBe('stepped_shaft.estimated.step');
  });

  it('folds letters that ASCII does not have', () => {
    expect(estimatedStepFilename('şaft')).toBe('saft.estimated.step');
    expect(estimatedStepFilename('ığüöçĞÜÖÇİ')).toBe('iguocGUOCI.estimated.step');
  });

  it('folds the dotless i, which decomposes into nothing', () => {
    expect(estimatedStepFilename('ısıtıcı mili')).toBe('isitici_mili.estimated.step');
  });

  it('removes what a file name or a header cannot carry', () => {
    // A quote would end the filename= value early; a slash would make the
    // name a path.
    expect(estimatedStepFilename('a"b')).toBe('a_b.estimated.step');
    expect(estimatedStepFilename('../../etc/passwd')).toBe('etc_passwd.estimated.step');
    expect(estimatedStepFilename('two\nlines')).toBe('two_lines.estimated.step');
  });

  it('does not leave runs of separators behind', () => {
    expect(estimatedStepFilename('mil   sonu   somunu')).toBe(
      'mil_sonu_somunu.estimated.step',
    );
    expect(estimatedStepFilename('  şaft  ')).toBe('saft.estimated.step');
  });

  it('keeps the punctuation a part name legitimately uses', () => {
    expect(estimatedStepFilename('rev.2-final_v3')).toBe('rev.2-final_v3.estimated.step');
  });

  it('names something rather than nothing', () => {
    // A file called ".estimated.step" is hidden on macOS and Linux, and says
    // nothing about which part it is.
    expect(estimatedStepFilename('')).toBe('part.estimated.step');
    expect(estimatedStepFilename('東')).toBe('part.estimated.step');
    expect(estimatedStepFilename('___')).toBe('part.estimated.step');
  });
});
