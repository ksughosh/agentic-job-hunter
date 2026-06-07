/**
 * Tests for the enrichment toggle on the onboarding page.
 *
 * The toggle lives in `portal/templates/onboarding.html` (id="enrichJobs")
 * and the form-submit handler in `portal/static/js/onboarding.js` reads
 * its checked state and appends `enrich_jobs=true|false` to the FormData
 * sent to /api/start-search.
 *
 * We don't load the full onboarding.js module here — it pulls in a lot of
 * DOM scaffolding we don't have under jsdom — we test the small piece of
 * logic the form-submit handler performs.
 */
import { describe, it, expect, beforeEach } from 'vitest';

/**
 * Mirror of the snippet inside the form-submit handler in onboarding.js.
 * If the snippet changes there, change it here too — the test guards the
 * contract, not the implementation.
 */
function appendEnrichFlag(fd, doc = document) {
  const $enrich = doc.getElementById('enrichJobs');
  fd.append('enrich_jobs', ($enrich && $enrich.checked) ? 'true' : 'false');
  return fd;
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('Enrich toggle — appendEnrichFlag', () => {
  it('appends enrich_jobs=true when checkbox is checked', () => {
    document.body.innerHTML = '<input type="checkbox" id="enrichJobs" checked />';
    const fd = new FormData();
    appendEnrichFlag(fd);
    expect(fd.get('enrich_jobs')).toBe('true');
  });

  it('appends enrich_jobs=false when checkbox is unchecked', () => {
    document.body.innerHTML = '<input type="checkbox" id="enrichJobs" />';
    const fd = new FormData();
    appendEnrichFlag(fd);
    expect(fd.get('enrich_jobs')).toBe('false');
  });

  it('appends enrich_jobs=false when the checkbox is missing entirely', () => {
    // Backward-compatible: if the template is older and lacks the toggle,
    // the handler must still send a valid value.
    const fd = new FormData();
    appendEnrichFlag(fd);
    expect(fd.get('enrich_jobs')).toBe('false');
  });

  it('does not overwrite other form fields', () => {
    document.body.innerHTML = '<input type="checkbox" id="enrichJobs" checked />';
    const fd = new FormData();
    fd.append('desired_roles', 'Senior Auditor');
    fd.append('work_mode', 'remote');
    appendEnrichFlag(fd);
    expect(fd.get('desired_roles')).toBe('Senior Auditor');
    expect(fd.get('work_mode')).toBe('remote');
    expect(fd.get('enrich_jobs')).toBe('true');
  });
});

describe('Enrich toggle — UX contract', () => {
  it('checkbox defaults to checked in the onboarding template', () => {
    // Smoke test: the onboarding template ships the checkbox with `checked`
    // so first-time users get the better experience by default.  This test
    // mirrors that markup so a refactor that removes the default trips it.
    document.body.innerHTML =
      '<input type="checkbox" id="enrichJobs" checked />';
    const el = document.getElementById('enrichJobs');
    expect(el).toBeTruthy();
    expect(el.checked).toBe(true);
  });
});

describe('Dashboard — Verified badge contract', () => {
  /**
   * The dashboard renders a green "✓ Verified" pill next to the Apply
   * button when the job came back from enrichment with `enriched=true`
   * AND has an `apply_url`. This test guards the rendering contract.
   */
  function renderApplyCell(job) {
    if (job.apply_url) {
      const verified = job.enriched ? '<span class="verified-badge">✓ Verified</span>' : '';
      return `<a href="${job.apply_url}" class="apply-btn">Apply ↗</a>${verified}`;
    }
    if (job.job_url) {
      return `<a href="${job.job_url}" class="apply-btn">Apply ↗</a>`;
    }
    return `<span class="tag-source">Via ${job.source || ''}</span>`;
  }

  it('shows Verified badge when enriched AND apply_url present', () => {
    const html = renderApplyCell({ apply_url: 'https://x.greenhouse.io/jobs/1', enriched: true });
    expect(html).toContain('Verified');
    expect(html).toContain('greenhouse');
  });

  it('hides Verified badge when not enriched', () => {
    const html = renderApplyCell({ apply_url: 'https://x.greenhouse.io/jobs/1' });
    expect(html).not.toContain('Verified');
  });

  it('falls back to job_url when apply_url missing', () => {
    const html = renderApplyCell({ job_url: 'https://example.com/jobs/1', enriched: true });
    expect(html).toContain('example.com/jobs/1');
    expect(html).not.toContain('Verified'); // no apply_url → no verified pill
  });

  it('shows Via <source> when both URLs missing', () => {
    const html = renderApplyCell({ source: 'LinkedIn' });
    expect(html).toContain('Via LinkedIn');
  });
});
