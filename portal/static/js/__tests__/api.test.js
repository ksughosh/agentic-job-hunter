/**
 * Tests for the Api client (portal/static/js/api.js).
 * Verifies every endpoint hits the right URL, method, and body shape.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

const Api = require('../api.js');

function lastCall() {
  return global.fetch.mock.calls[global.fetch.mock.calls.length - 1];
}

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ json: () => Promise.resolve({ ok: true }) })
  );
});

describe('Api — module shape', () => {
  it('exposes the expected surface', () => {
    const expected = [
      'getUsers', 'createUser', 'switchUser', 'deleteUser',
      'scanResume', 'startSearch', 'searchStatus', 'refresh', 'refreshStatus', 'cancelPipeline',
      'checkOllama', 'getProviders', 'setProvider', 'setApiKey', 'setGroqKey',
      'generateResume', 'generateCover',
      'getSearchProfiles', 'createSearchProfile', 'updateSearchProfile', 'switchSearchProfile', 'deleteSearchProfile',
      'getSources', 'addSource', 'removeSource', 'toggleSource',
    ];
    for (const k of expected) expect(typeof Api[k]).toBe('function');
  });
});

describe('Api — GET endpoints', () => {
  it('getUsers hits /api/users', async () => {
    await Api.getUsers();
    const [url, opts] = lastCall();
    expect(url).toBe('/api/users');
    // GET: no method/body set on opts
    expect(opts?.method).toBeUndefined();
    expect(opts?.body).toBeUndefined();
  });

  it('getProviders hits /api/providers', async () => {
    await Api.getProviders();
    expect(lastCall()[0]).toBe('/api/providers');
  });

  it('searchStatus hits /api/search-status', async () => {
    await Api.searchStatus();
    expect(lastCall()[0]).toBe('/api/search-status');
  });
});

describe('Api — POST endpoints', () => {
  it('createUser posts JSON body with name', async () => {
    await Api.createUser('alice');
    const [url, opts] = lastCall();
    expect(url).toBe('/api/users/create');
    expect(opts.method).toBe('POST');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(opts.body)).toEqual({ name: 'alice' });
  });

  it('setProvider posts provider id', async () => {
    await Api.setProvider('groq');
    const [url, opts] = lastCall();
    expect(url).toBe('/api/set-provider');
    expect(JSON.parse(opts.body)).toEqual({ provider: 'groq' });
  });

  it('setGroqKey posts the key', async () => {
    await Api.setGroqKey('gsk_test123');
    const [url, opts] = lastCall();
    expect(url).toBe('/api/set-groq-key');
    expect(JSON.parse(opts.body)).toEqual({ key: 'gsk_test123' });
  });

  it('toggleSource posts name + enabled', async () => {
    await Api.toggleSource('linkedin', true);
    const [url, opts] = lastCall();
    expect(url).toBe('/api/sources/toggle');
    expect(JSON.parse(opts.body)).toEqual({ name: 'linkedin', enabled: true });
  });

  it('switchUser interpolates the id into the URL', async () => {
    await Api.switchUser('abc123');
    expect(lastCall()[0]).toBe('/api/users/switch/abc123');
  });

  it('updateSearchProfile interpolates id and posts payload', async () => {
    await Api.updateSearchProfile('p1', { name: 'foo' });
    const [url, opts] = lastCall();
    expect(url).toBe('/api/search-profiles/update/p1');
    expect(JSON.parse(opts.body)).toEqual({ name: 'foo' });
  });
});

describe('Api — form-data endpoints', () => {
  it('scanResume posts FormData without JSON header', async () => {
    const fd = new FormData();
    fd.append('resume', new Blob(['x']), 'r.pdf');
    await Api.scanResume(fd);
    const [url, opts] = lastCall();
    expect(url).toBe('/api/scan-resume');
    expect(opts.method).toBe('POST');
    expect(opts.body).toBe(fd);
    // No content-type — fetch sets multipart boundary itself
    expect(opts.headers).toBeUndefined();
  });

  it('startSearch posts FormData', async () => {
    const fd = new FormData();
    await Api.startSearch(fd);
    expect(lastCall()[0]).toBe('/api/start-search');
    expect(lastCall()[1].body).toBe(fd);
  });
});
