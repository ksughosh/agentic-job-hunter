/**
 * Tests for ResumeBuilder.build — verifies it parses LLM-generated resume text
 * into a complete HTML document with the right sections, fallbacks, and limits.
 */
import { describe, it, expect } from 'vitest';

const ResumeBuilder = require('../resume-builder.js');

const SAMPLE_RESUME = `
JANE DOE
Staff Mobile Engineer
jane@example.com | +1 555 123 4567 | San Francisco

SUMMARY
Mobile engineer with 10 years building Android and iOS apps at scale.

SKILLS
Kotlin, Swift, Jetpack Compose, SwiftUI, Coroutines, Espresso, CI/CD, GraphQL

EXPERIENCE
Staff Android Engineer
Acme Corp | Jan 2022 – Present
• Shipped feature reducing crash rate by 35%
• Led migration to Jetpack Compose across 8 teams

Senior Android Engineer
Globex | 2019 – 2022
• Built modular architecture serving 5M users
• Mentored 6 engineers

EDUCATION
MSc Computer Science — Stanford, 2015
`;

const PROFILE = {
    name: 'Jane Doe',
    title: 'Staff Mobile Engineer',
    email: 'jane@example.com',
    phone: '+1 555 123 4567',
    location: 'San Francisco',
    primary_skills: ['Kotlin', 'Swift', 'Jetpack Compose'],
};

describe('ResumeBuilder.build', () => {
    it('exposes a build function', () => {
        expect(typeof ResumeBuilder.build).toBe('function');
    });

    it('returns a complete HTML document', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        expect(html).toMatch(/<\/body>/);
        expect(html).toMatch(/<\/html>/);
    });

    it('extracts the name into the rendered HTML', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        expect(html).toContain('JANE DOE');
    });

    it('includes summary text', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        expect(html).toContain('Mobile engineer with 10 years');
    });

    it('renders skill pills', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        expect(html).toContain('Kotlin');
        expect(html).toContain('Swift');
    });

    it('renders multiple experience entries', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        expect(html).toContain('Acme Corp');
        expect(html).toContain('Globex');
    });

    it('renders education', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        expect(html).toContain('Stanford');
    });

    it('falls back to profile when text omits name', () => {
        const html = ResumeBuilder.build('SUMMARY\nNo name in this text.', { name: 'fallback name' });
        expect(html).toContain('FALLBACK NAME');
    });

    it('handles case-insensitive section headers', () => {
        const html = ResumeBuilder.build(
            'JOHN\nDev\njohn@x.com | 555 | NYC\n\nprofessional summary\nGreat dev.\n\ntechnical skills\nGo, Rust',
            { primary_skills: ['Go'] }
        );
        expect(html).toContain('Great dev');
        expect(html).toContain('Go');
    });

    it('does not render achievements section when none provided', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, PROFILE);
        // No achievements section in SAMPLE_RESUME → "Achievements" heading should be absent
        expect(html).not.toMatch(/>Achievements</);
    });

    it('caps skills at 10', () => {
        const many = 'JOHN\nDev\njohn@x.com\n\nSKILLS\n' +
            Array.from({ length: 25 }, (_, i) => `Skill${i}`).join(', ');
        const html = ResumeBuilder.build(many, {});
        // Count rendered skill pills (best-effort: count Skill0, Skill1, ... up to N)
        const count = (html.match(/Skill\d+/g) || []).length;
        expect(count).toBeLessThanOrEqual(10);
        expect(count).toBeGreaterThan(0);
    });

    it('tolerates empty profile', () => {
        const html = ResumeBuilder.build(SAMPLE_RESUME, {});
        expect(html).toContain('JANE DOE');
    });

    it('tolerates empty text by using profile fallback', () => {
        const html = ResumeBuilder.build('', PROFILE);
        expect(html).toContain('JANE DOE');
    });
});
