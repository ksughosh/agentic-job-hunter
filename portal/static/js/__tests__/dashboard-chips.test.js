/**
 * Tests for DashboardVM._buildChips — verifies chips are derived dynamically
 * from window.__dashboardData.profile and the jobs list, not hardcoded.
 */
import { describe, it, expect, beforeEach } from 'vitest';

// Stub deps before module load — dashboard.js references Api/Chart at import time.
global.Api = {};
global.Chart = class { constructor() {} update() {} destroy() {} };

const { DashboardVM } = require('../dashboard.js');

function setup({ profile = {}, jobs = [] } = {}) {
    window.__JH_TEST__ = true;
    window.__dashboardData = {
        profile,
        jobs,
        scoreDistribution: {}, sourceCounts: {}, typeCounts: {},
        salaryRanges: {}, topSkills: [], sourceQualityData: {},
    };
    // _buildChips reads from the inner `jobsData` cache, which is set by init().
    // Init touches DOM heavily; we shortcut by reading directly from __dashboardData.
}

beforeEach(() => {
    document.body.innerHTML = '<div id="filterChips"></div>';
    setup();
});

describe('DashboardVM._buildChips — dynamic chip generation', () => {
    it('always includes the "All" chip first', () => {
        setup({ profile: {}, jobs: [] });
        const chips = DashboardVM._buildChips();
        expect(chips[0].id).toBe('all');
        expect(chips[0].label).toBe('All');
    });

    it('omits Contract chip when no contract jobs exist', () => {
        setup({ profile: {}, jobs: [{ job_type: 'full-time' }] });
        const ids = DashboardVM._buildChips().map(c => c.id);
        expect(ids).not.toContain('contract');
    });

    it('adds Contract chip when any job is contract', () => {
        setup({ profile: {}, jobs: [{ job_type: 'Contract' }, { job_type: 'full-time' }] });
        const ids = DashboardVM._buildChips().map(c => c.id);
        expect(ids).toContain('contract');
    });

    it('omits High Match chip when no job ≥60% match', () => {
        setup({ profile: {}, jobs: [{ match_score: 45 }] });
        const ids = DashboardVM._buildChips().map(c => c.id);
        expect(ids).not.toContain('high-match');
    });

    it('adds High Match chip when at least one job ≥60%', () => {
        setup({ profile: {}, jobs: [{ match_score: 72 }] });
        const ids = DashboardVM._buildChips().map(c => c.id);
        expect(ids).toContain('high-match');
    });

    it('adds GenAI/AI chip when profile skills include LLM/GenAI keywords', () => {
        setup({ profile: { primary_skills: ['LLM', 'Python', 'RAG'] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('GenAI/AI');
    });

    it('adds Mobile chip when profile skills include Android/Kotlin', () => {
        setup({ profile: { primary_skills: ['Kotlin', 'Jetpack Compose', 'Android'] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Mobile');
    });

    it('omits Mobile chip when profile is purely backend', () => {
        setup({ profile: { primary_skills: ['Python', 'PostgreSQL', 'Kubernetes'] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).not.toContain('Mobile');
    });

    it('adds Staff+ chip only for staff/principal experience_level', () => {
        setup({ profile: { experience_level: 'staff' } });
        expect(DashboardVM._buildChips().map(c => c.id)).toContain('staff');

        setup({ profile: { experience_level: 'principal' } });
        expect(DashboardVM._buildChips().map(c => c.id)).toContain('staff');

        setup({ profile: { experience_level: 'mid' } });
        expect(DashboardVM._buildChips().map(c => c.id)).not.toContain('staff');
    });

    it('adds Senior+ chip for senior level (not Staff+)', () => {
        setup({ profile: { experience_level: 'senior' } });
        const ids = DashboardVM._buildChips().map(c => c.id);
        expect(ids).toContain('senior');
        expect(ids).not.toContain('staff');
    });

    it('derives multiple domain chips when skills span domains', () => {
        setup({
            profile: { primary_skills: ['Kotlin', 'Python', 'Kubernetes', 'LLM'] }
        });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Mobile');
        expect(labels).toContain('Backend');
        expect(labels).toContain('DevOps');
        expect(labels).toContain('GenAI/AI');
    });

    it('respects profile.domain even when skills are sparse', () => {
        setup({ profile: { domain: 'mobile', primary_skills: [] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Mobile');
    });

    // ── Non-tech professions (regression: previously the dashboard only
    //    knew tech clusters, so a Chartered Accountant saw 'All' + nothing).

    it('adds Finance / Audit chips for a Chartered Accountant resume', () => {
        setup({
            profile: {
                domain: 'finance',
                primary_skills: [
                    'Statutory Audit', 'Tax Audit', 'GST', 'ROC Filings',
                    'Concurrent Audit', 'Tally ERP', 'Finacle'
                ]
            }
        });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Finance');
        expect(labels).toContain('Audit');
        // And critically, NO engineering chip:
        expect(labels).not.toContain('Mobile');
        expect(labels).not.toContain('Backend');
        expect(labels).not.toContain('GenAI/AI');
    });

    it('adds Legal chip for a lawyer profile', () => {
        setup({ profile: { primary_skills: ['Litigation', 'Contracts', 'IPR'] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Legal');
    });

    it('adds Marketing chip for a digital marketer', () => {
        setup({ profile: { primary_skills: ['SEO', 'Content', 'Brand', 'Social Media'] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Marketing');
    });

    it('adds Design chip for a UX designer', () => {
        setup({ profile: { primary_skills: ['Figma', 'UX', 'Visual Design'] } });
        const labels = DashboardVM._buildChips().map(c => c.label);
        expect(labels).toContain('Design');
    });

    it('returns only "All" for an empty profile with no jobs', () => {
        setup({ profile: {}, jobs: [] });
        const chips = DashboardVM._buildChips();
        expect(chips.length).toBe(1);
        expect(chips[0].id).toBe('all');
    });
});
