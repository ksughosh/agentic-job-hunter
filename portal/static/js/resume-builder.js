/**
 * Resume HTML Builder
 * Parses LLM-generated text resume → formatted HTML for iframe rendering.
 */
const ResumeBuilder = (() => {

    // Skill ratings are derived from profile at render time — no hardcoded list.
    // Primary skills get 90+, secondary skills 70-85, rest 60.
    let _profileSkillRatings = {};

    function _buildSkillRatings(profile) {
        const ratings = {};
        const primary = (profile.primary_skills || []).map(s => s.toLowerCase());
        return function(skillName) {
            const key = skillName.toLowerCase();
            if (_profileSkillRatings[key] !== undefined) return _profileSkillRatings[key];
            const idx = primary.indexOf(key);
            let score;
            if (idx >= 0) score = Math.max(75, 95 - idx * 3);  // primary: 95, 92, 89...
            else score = 65;
            _profileSkillRatings[key] = score;
            return score;
        };
    }

    /**
     * Build formatted resume HTML from LLM-generated text.
     * @param {string} text - LLM-generated plain text resume
     * @param {object} profile - Candidate profile (from scan/parse) for fallback data
     * @returns {string} Complete HTML document string
     */
    function build(text, profile) {
        profile = profile || {};
        const getSkillRating = _buildSkillRatings(profile);
        const lines = text.split('\n');
        let name = '', subtitle = '', contact = '', summary = '', skills = [];
        let experience = [], education = [], achievements = [], additional = [];
        let section = '';

        for (const line of lines) {
            const t = line.trim();
            if (!t) continue;
            // Detect section headers (case-insensitive, with or without symbols)
            const headerMatch = t.replace(/[#=\-_*:]/g, '').trim().toUpperCase();
            if (headerMatch === 'SUMMARY' || headerMatch === 'PROFESSIONAL SUMMARY' || headerMatch === 'PROFILE') { section = 'summary'; continue; }
            if (headerMatch === 'SKILLS' || headerMatch === 'TECHNICAL SKILLS' || headerMatch === 'CORE SKILLS') { section = 'skills'; continue; }
            if (headerMatch === 'EXPERIENCE' || headerMatch === 'WORK EXPERIENCE' || headerMatch === 'PROFESSIONAL EXPERIENCE') { section = 'experience'; continue; }
            if (headerMatch === 'EDUCATION' || headerMatch === 'QUALIFICATIONS') { section = 'education'; continue; }
            if (headerMatch === 'ACHIEVEMENTS' || headerMatch === 'AWARDS' || headerMatch === 'CERTIFICATIONS') { section = 'achievements'; continue; }
            if (headerMatch === 'ADDITIONAL' || headerMatch === 'OTHER' || headerMatch === 'LANGUAGES') { section = 'additional'; continue; }
            // Name: first non-section, non-empty line (< 60 chars, not a bullet)
            if (!name && !section && t.length < 60 && !t.startsWith('•') && !t.startsWith('-') && /[A-Za-z]/.test(t)) { name = t; continue; }
            // Subtitle: second such line
            if (name && !subtitle && !section && !t.includes('═══') && !t.includes('@') && t.length < 80 && !t.startsWith('•')) { subtitle = t; continue; }
            // Contact: contains @ or phone patterns
            if ((t.includes('@') || /\d{3}[\s\-]?\d{3}/.test(t)) && t.includes('|') && !contact) { contact = t; continue; }
            if (t.startsWith('Key expertise:')) { summary += ' ' + t; continue; }
            if (section === 'summary')     summary += t + ' ';
            else if (section === 'skills')      skills.push(t);
            else if (section === 'experience')  experience.push(t);
            else if (section === 'education')   education.push(t);
            else if (section === 'achievements') achievements.push(t);
            else if (section === 'additional')  additional.push(t);
        }

        // Fallback to profile data when parser can't extract from LLM text.
        if (!name) name = (profile.name || 'Candidate').toUpperCase();
        if (!subtitle) subtitle = profile.title || '';
        if (!contact) {
            const parts = [];
            if (profile.email) parts.push(profile.email);
            if (profile.phone) parts.push(profile.phone);
            if (profile.location) parts.push(profile.location);
            contact = parts.join(' | ');
        }

        const expEntries = _parseExperience(experience);
        const skillPills = _parseSkills(skills);
        const eduHTML    = _parseEducation(education);
        const achHTML    = achievements.map(a => a.replace(/^[•\-\s]+/, '').trim()).filter(Boolean).map(a => `<li>${a}</li>`).join('');
        const { langs, details } = _parseAdditional(additional);
        const contactParts = contact.split('|').map(c => c.trim()).filter(Boolean);

        // Enforce limits: max 10 skills, max 4 experience entries
        const skillPillsHTML = skillPills.slice(0, 10).map(s => {
            const pct = getSkillRating(s);
            return `<div class="skill-pill">${s}<div class="skill-bar"><div class="skill-bar-fill" style="width:${pct}%"></div></div></div>`;
        }).join('');

        const expHTML = expEntries.slice(0, 4).map(e => {
            // Enforce max 3 bullets per entry
            const bullets = e.bullets.slice(0, 3);
            const dp = e.detail.split('|');
            return `<div class="exp-entry">
                <div class="exp-header"><div class="exp-title">${e.title}</div><div class="exp-dates">${(dp[1] || '').trim()}</div></div>
                <div class="exp-company">${(dp[0] || '').trim()}</div>
                <ul class="exp-bullets">${bullets.map(b => '<li>' + b + '</li>').join('')}</ul>
            </div>`;
        }).join('');

        return _template(name, subtitle, contactParts, summary.trim(), skillPillsHTML, eduHTML, achHTML, langs, expHTML, profile);
    }

    function _parseExperience(lines) {
        const entries = []; let cur = null;
        for (const t of lines.map(l => l.trim())) {
            if (t.startsWith('•')) { if (cur) cur.bullets.push(t.substring(1).trim()); }
            else if (t.includes('|'))   { if (cur) cur.detail = t; }
            else if (t.length > 5)      { if (cur) entries.push(cur); cur = { title: t, detail: '', bullets: [] }; }
        }
        if (cur) entries.push(cur);
        return entries;
    }

    function _parseSkills(lines) {
        const pills = [];
        for (const s of lines) {
            const parts = s.split(':');
            if (parts.length >= 2) {
                parts.slice(1).join(':').split('·').map(x => x.trim()).filter(Boolean).forEach(item => pills.push(item));
            } else {
                s.split(/[·,]/).map(x => x.trim()).filter(Boolean).forEach(item => pills.push(item));
            }
        }
        return pills;
    }

    function _parseEducation(lines) {
        let html = '';
        for (const e of lines) {
            const t = e.trim();
            if (t.startsWith('MSc') || t.startsWith('B.E.') || t.startsWith('B.E ')) {
                const parts = t.split('—');
                const degree = parts[0].trim();
                const rest = parts[1] ? parts[1].trim() : '';
                html += `<div class="edu-entry"><div class="edu-degree">${degree}</div><div class="edu-uni">${rest.split(',')[0] || ''}</div><div class="edu-loc">${rest.split(',').slice(1).join(',').trim()}</div></div>`;
            } else if (t.startsWith('Thesis:')) {
                html += `<div class="edu-thesis">${t}</div>`;
            }
        }
        return html;
    }

    function _parseAdditional(lines) {
        let langs = '', details = '';
        for (const a of lines) {
            if (a.includes('Languages:')) langs = a.replace('Languages:', '').trim();
            if (a.includes('Visa:') || a.includes('Nationality:')) details += `<li>${a.trim()}</li>`;
        }
        return { langs, details };
    }

    function _template(name, subtitle, contactParts, summary, skillPillsHTML, eduHTML, achHTML, langs, expHTML, profile) {
        profile = profile || {};
        const contactHTML = contactParts.map((c, i) =>
            (i > 0 ? '<span class="contact-sep">|</span>' : '') +
            (c.includes('@') ? `<a href="mailto:${c}">${c}</a>` :
             c.includes('linkedin') ? `<a href="https://${c.trim()}">${c.trim().split('/').pop()}</a>` :
             `<span>${c}</span>`)
        ).join('');

        // Summary tagline from profile domain, not hardcoded.
        const domain = profile.domain || 'Software Engineering';
        const tagline = subtitle || `${domain} Professional`;

        // Details section from profile (no hardcoded nationality/visa).
        let detailsHTML = '';
        if (profile.location) detailsHTML += `<div class="detail-item-side">${profile.location}</div>`;

        return `<!DOCTYPE html><html><head>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600;700;800&display=swap">
        <style>
            *{margin:0;padding:0;box-sizing:border-box;}
            body{font-family:'Nunito Sans','Avenir','Helvetica Neue',Helvetica,Arial,sans-serif;color:#333;background:white;font-size:11px;line-height:1.5;}
            @media print{body{-webkit-print-color-adjust:exact;print-color-adjust:exact;}@page{margin:0;size:A4;}.page{height:100vh;overflow:hidden;}}
            .page{max-width:850px;height:1122px;margin:0 auto;background:white;overflow:hidden;}
            .header{padding:32px 36px 0;}.name{font-size:28px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#222;border-bottom:2px solid #222;padding-bottom:6px;display:inline-block;}.title-line{font-size:12px;font-weight:300;color:#555;margin-top:4px;letter-spacing:0.5px;}
            .contact-bar{background:#d4a96a;color:white;padding:8px 36px;font-size:11px;font-weight:600;margin-top:12px;display:flex;gap:16px;flex-wrap:wrap;align-items:center;}.contact-bar a{color:white;text-decoration:none;}.contact-sep{opacity:0.6;}
            .summary-section{padding:16px 36px 12px;border-bottom:1px solid #e0e0e0;}.summary-tag{font-size:12px;font-weight:700;color:#222;margin-bottom:6px;}.summary-text{font-size:11px;color:#444;line-height:1.7;}
            .body{display:grid;grid-template-columns:220px 1fr;padding:0;}
            .sidebar{padding:20px 20px 30px 36px;border-right:1px solid #e8e8e8;}
            .side-section-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#d4a96a;margin:18px 0 10px;text-decoration:underline;text-decoration-color:#d4a96a;text-underline-offset:4px;}.side-section-title:first-child{margin-top:0;}
            .skill-pill{display:block;font-size:11px;color:#333;padding:6px 0 10px;font-weight:400;}.skill-bar{height:3px;background:#ddd;border-radius:2px;margin-top:4px;overflow:hidden;}.skill-bar-fill{height:100%;background:#d4a96a;border-radius:2px;}
            .edu-entry{margin-bottom:10px;}.edu-degree{font-size:12px;font-weight:700;color:#222;}.edu-uni{font-size:10px;color:#666;}.edu-loc{font-size:10px;color:#888;}.edu-thesis{font-size:10px;color:#666;font-style:italic;margin-top:-6px;margin-bottom:10px;}
            .side-list{list-style:disc;padding-left:16px;}.side-list li{font-size:10.5px;color:#444;margin-bottom:3px;line-height:1.5;}
            .lang-item{font-size:10.5px;color:#444;margin-bottom:2px;}.detail-item-side{font-size:10.5px;color:#444;margin-bottom:2px;}
            .main{padding:20px 36px 30px 24px;}.main-section-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#d4a96a;margin:0 0 14px;text-decoration:underline;text-decoration-color:#d4a96a;text-underline-offset:4px;}
            .exp-entry{margin-bottom:18px;}.exp-header{display:flex;justify-content:space-between;align-items:baseline;}.exp-title{font-size:13px;font-weight:700;color:#222;}.exp-dates{font-size:10px;color:#888;white-space:nowrap;}.exp-company{font-size:10.5px;color:#666;margin-top:1px;}.exp-bullets{margin:6px 0 0 16px;font-size:10.5px;color:#333;line-height:1.65;}.exp-bullets li{margin-bottom:2px;}.exp-bullets li::marker{color:#d4a96a;}
        </style></head><body>
        <div class="page">
            <div class="header"><div class="name">${name}</div><div class="title-line">${subtitle}</div></div>
            <div class="contact-bar">${contactHTML}</div>
            <div class="summary-section"><div class="summary-tag">${tagline}</div><div class="summary-text">${summary}</div></div>
            <div class="body">
                <div class="sidebar">
                    <div class="side-section-title">Skills</div>${skillPillsHTML}
                    <div class="side-section-title">Education</div>${eduHTML}
                    ${achHTML ? `<div class="side-section-title">Achievements</div><ul class="side-list">${achHTML}</ul>` : ''}
                    ${langs ? `<div class="side-section-title">Languages</div>${langs.split(/[·,]/).map(l => '<div class="lang-item">' + l.trim() + '</div>').join('')}` : ''}
                    ${detailsHTML ? `<div class="side-section-title">Details</div>${detailsHTML}` : ''}
                </div>
                <div class="main"><div class="main-section-title">Experience</div>${expHTML}</div>
            </div>
        </div>
        </body></html>`;
    }

    return { build };
})();
