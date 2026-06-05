/**
 * Tests for SearchProfiles module — focuses on pure helpers and module shape.
 * Heavy DOM interaction paths covered separately by E2E (when added).
 */
import { describe, it, expect } from 'vitest';

// Stub the global Api dep that SearchProfiles references at module load.
global.Api = {};

const SearchProfiles = require('../search-profiles.js');

describe('SearchProfiles — module shape', () => {
    it('exposes the public surface', () => {
        for (const k of ['init', 'load', 'switchProfile', 'remove',
                         'showCreate', 'showEdit', 'hideModal', 'save']) {
            expect(typeof SearchProfiles[k]).toBe('function');
        }
    });
});

describe('SearchProfiles._modeIcon', () => {
    it('returns the globe glyph for remote', () => {
        expect(SearchProfiles._modeIcon('remote')).toBe('&#x1f30d;');
    });

    it('returns the office glyph for hybrid', () => {
        expect(SearchProfiles._modeIcon('hybrid')).toBe('&#x1f3e2;');
    });

    it('returns the pin glyph as default', () => {
        expect(SearchProfiles._modeIcon('onsite')).toBe('&#x1f4cd;');
        expect(SearchProfiles._modeIcon('anything-else')).toBe('&#x1f4cd;');
        expect(SearchProfiles._modeIcon('')).toBe('&#x1f4cd;');
    });
});

describe('SearchProfiles._esc', () => {
    it('escapes single quotes for safe attribute embedding', () => {
        expect(SearchProfiles._esc("O'Brien")).toBe("O\\'Brien");
    });

    it('escapes < to prevent HTML injection', () => {
        expect(SearchProfiles._esc('<script>')).toBe('&lt;script>');
    });

    it('tolerates null/undefined input', () => {
        expect(SearchProfiles._esc(null)).toBe('');
        expect(SearchProfiles._esc(undefined)).toBe('');
    });

    it('passes through plain strings unchanged', () => {
        expect(SearchProfiles._esc('hello world')).toBe('hello world');
    });
});
