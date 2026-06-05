/**
 * Dashboard ViewModel
 * Manages: charts, filters, sorting, refresh pipeline,
 * document generation modal, and LLM provider toggle.
 *
 * Expects the View to set window.__dashboardData and window.__serverState
 * before this script loads.
 */
const DashboardVM = (() => {

    // Data injected by the template
    let jobsData, scoreData, sourceData, typeData, salaryData, skillsData, sqData;

    // ── Init ──

    function init() {
        const d = window.__dashboardData || {};
        jobsData   = d.jobs || [];
        scoreData  = d.scoreDistribution || {};
        sourceData = d.sourceCounts || {};
        typeData   = d.typeCounts || {};
        salaryData = d.salaryRanges || {};
        skillsData = d.topSkills || [];
        sqData     = d.sourceQualityData || {};

        _initCharts();
        _bindSearch();
        _resumePipelineIfRunning();
        _renderProviders();
        _renderChips();
    }

    async function _renderProviders() {
        const el = document.getElementById('providerPills');
        if (!el) return;
        try {
            const d = await Api.getProviders();
            const active = d.active;
            el.innerHTML = '';
            (d.providers || []).forEach((p, idx) => {
                const btn = document.createElement('button');
                btn.className = 'refresh-btn';
                btn.style.cssText = 'padding:6px 12px; font-size:11px; margin:0; border:1px solid var(--border);';
                btn.style.borderRadius = idx === 0 ? '6px 0 0 6px' : (idx === d.providers.length - 1 ? '0 6px 6px 0' : '0');
                btn.style.background = (p.id === active) ? 'rgba(108,92,231,0.3)' : 'var(--surface2)';
                btn.style.opacity = p.available ? '1' : '0.45';
                btn.title = p.detail || '';
                btn.textContent = `${p.icon} ${p.label}`;
                btn.disabled = !p.available;
                btn.onclick = () => switchProvider(p.id);
                el.appendChild(btn);
            });
        } catch (e) { console.error('providers fetch failed', e); }
    }

    // ── Charts ──

    const COLORS = ['#6c5ce7','#00cec9','#fdcb6e','#e17055','#74b9ff','#fd79a8','#a29bfe','#55efc4'];

    function _initCharts() {
        Chart.defaults.color = '#9999aa';
        Chart.defaults.borderColor = '#2d2d3d';
        Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif";

        _bar('scoreChart', scoreData, ['#00cec9','#6c5ce7','#fdcb6e','#e17055','#636e72']);
        _doughnut('sourceChart', sourceData, COLORS);
        _doughnut('typeChart', typeData, COLORS.slice(2));
        _bar('salaryChart', salaryData, ['#00cec9','#6c5ce7','#a29bfe','#fdcb6e','#636e72']);
        if (skillsData.length) _horizontalBar('skillsChart', skillsData);
        if (Object.keys(sqData).length) _sourceQualityChart();
    }

    function _bar(id, data, colors) {
        new Chart(document.getElementById(id), {
            type: 'bar',
            data: { labels: Object.keys(data), datasets: [{ data: Object.values(data), backgroundColor: colors, borderRadius: 6 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, grid: { color: '#2d2d3d' } } } }
        });
    }

    function _doughnut(id, data, colors) {
        new Chart(document.getElementById(id), {
            type: 'doughnut',
            data: { labels: Object.keys(data), datasets: [{ data: Object.values(data), backgroundColor: colors, borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { padding: 12, usePointStyle: true } } }, cutout: '60%' }
        });
    }

    function _horizontalBar(id, data) {
        new Chart(document.getElementById(id), {
            type: 'bar',
            data: { labels: data.map(s => s[0]), datasets: [{ data: data.map(s => s[1]), backgroundColor: '#6c5ce7', borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, grid: { color: '#2d2d3d' } } } }
        });
    }

    function _sourceQualityChart() {
        const labels  = Object.keys(sqData);
        const quality = labels.map(s => sqData[s].quality);
        const count   = labels.map(s => sqData[s].count);
        const colors  = quality.map(q => q >= 80 ? '#00cec9' : q >= 65 ? '#6c5ce7' : q >= 50 ? '#fdcb6e' : '#e17055');
        new Chart(document.getElementById('sourceQualityChart'), {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    { label: 'Quality Score', data: quality, backgroundColor: colors, borderRadius: 6, yAxisID: 'y' },
                    { label: 'Jobs Found', data: count, backgroundColor: 'rgba(162,155,254,0.3)', borderColor: '#a29bfe', borderWidth: 1, borderRadius: 4, yAxisID: 'y1' },
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'top', labels: { padding: 12, usePointStyle: true } } },
                scales: {
                    y:  { beginAtZero: true, max: 100, position: 'left', title: { display: true, text: 'Quality Score', color: '#9999aa' }, grid: { color: '#2d2d3d' } },
                    y1: { beginAtZero: true, position: 'right', title: { display: true, text: 'Jobs Found', color: '#9999aa' }, grid: { drawOnChartArea: false } },
                },
            }
        });
    }

    // ── LLM Provider ──

    async function switchProvider(provider) {
        const d = await Api.setProvider(provider);
        if (d.ok) _renderProviders();
    }

    // ── Refresh Pipeline ──

    function triggerRefresh() {
        const btn = document.getElementById('refreshBtn');
        btn.classList.add('loading'); btn.disabled = true;
        document.getElementById('refreshToast').classList.add('visible');
        Api.refresh().then(() => _pollRefresh());
    }

    function _pollRefresh() {
        Api.refreshStatus().then(data => {
            document.getElementById('toastMessage').textContent = data.message;
            document.getElementById('toastProgress').style.width = data.progress + '%';
            if (data.running) {
                setTimeout(_pollRefresh, 1500);
            } else {
                document.getElementById('toastTitle').textContent = data.progress >= 100 ? 'Complete!' : (data.message || 'Stopped');
                document.getElementById('refreshBtn').classList.remove('loading');
                document.getElementById('refreshBtn').disabled = false;
                document.getElementById('cancelRefreshBtn').style.display = 'none';
                setTimeout(() => { document.getElementById('refreshToast').classList.remove('visible'); window.location.reload(); }, 2000);
            }
        });
    }

    function cancelRefresh() {
        const btn = document.getElementById('cancelRefreshBtn');
        btn.disabled = true; btn.textContent = 'Cancelling...';
        Api.cancelPipeline().catch(() => {});
    }

    function _resumePipelineIfRunning() {
        const state = window.__serverState || {};
        if (state.pipelineRunning) {
            const btn = document.getElementById('refreshBtn');
            btn.classList.add('loading'); btn.disabled = true;
            document.getElementById('refreshToast').classList.add('visible');
            document.getElementById('toastTitle').textContent = 'Refreshing...';
            document.getElementById('toastMessage').textContent = state.pipelineMessage || 'Pipeline running...';
            document.getElementById('toastProgress').style.width = (state.pipelineProgress || 0) + '%';
            _pollRefresh();
        }
    }

    // ── Table: Expand, Filter, Sort, Search ──

    function toggleExpand(idx) {
        document.getElementById('expand-' + idx).classList.toggle('visible');
    }

    // ── Dynamic filter chips ─────────────────────────────────
    // Each chip is { id, label, test(row) → bool }. Generated from profile + jobs.

    // Domain keyword clusters — keys match `profile.domain` or are inferred from primary_skills.
    // Covers tech AND non-tech professions so the chips work for any candidate
    // (engineer, accountant, designer, lawyer, doctor, marketer, etc.).
    const DOMAIN_CLUSTERS = {
        // Tech
        'genai':     { label: 'GenAI/AI',   kw: ['genai','gen ai','llm','generative','rag','agentic','agent','prompt','transformer','gpt','claude'] },
        'ml-ai':     { label: 'ML/AI',      kw: ['ml','ai','machine learning','deep learning','tensorflow','pytorch','nlp','computer vision'] },
        'mobile':    { label: 'Mobile',     kw: ['android','ios','kotlin','swift','flutter','react native','jetpack','mobile','swiftui','compose'] },
        'backend':   { label: 'Backend',    kw: ['backend','python','golang','go ','java','node','rust','microservice','api','grpc'] },
        'full-stack':{ label: 'Full-Stack', kw: ['full stack','full-stack','react','typescript','next.js','vue','frontend'] },
        'devops':    { label: 'DevOps',     kw: ['devops','kubernetes','k8s','terraform','aws','gcp','azure','docker','ci/cd','infrastructure'] },
        'data':      { label: 'Data',       kw: ['data engineer','etl','spark','hadoop','airflow','snowflake','bigquery','warehouse','sql'] },
        'security':  { label: 'Security',   kw: ['security','infosec','pentest','vulnerability','cryptography','iam','zero trust'] },
        'product':   { label: 'Product',    kw: ['product manager','product owner','roadmap','stakeholder'] },
        // Non-tech professions
        'finance':   { label: 'Finance',    kw: ['finance','accounting','accountant','audit','auditor','tax','gst','cpa','chartered accountant','ca ','icai','financial','ledger','bookkeep','controller','treasury','reconcili'] },
        'audit':     { label: 'Audit',      kw: ['audit','auditor','statutory','concurrent','internal audit','external audit','compliance','sox','tax audit'] },
        'legal':     { label: 'Legal',      kw: ['legal','lawyer','attorney','litigation','counsel','paralegal','contracts','compliance','llp','llb','ipr'] },
        'medical':   { label: 'Medical',    kw: ['medical','clinical','doctor','physician','nurse','nursing','pharmacy','pharmacist','radiology','surgeon','healthcare'] },
        'design':    { label: 'Design',     kw: ['design','designer','ux','ui','visual','graphic','illustration','figma','sketch','adobe','branding','typography'] },
        'marketing': { label: 'Marketing',  kw: ['marketing','seo','sem','content','campaign','brand','social media','growth','digital marketing','copywriter'] },
        'sales':     { label: 'Sales',      kw: ['sales','account executive','bdr','sdr','business development','pipeline','quota','salesforce','crm'] },
        'hr':        { label: 'HR',         kw: ['human resources','hr ','recruiter','recruiting','talent','onboarding','people ops','employee'] },
        'operations':{ label: 'Operations', kw: ['operations','ops manager','supply chain','logistics','procurement','vendor','warehouse'] },
        'consulting':{ label: 'Consulting', kw: ['consulting','consultant','strategy','advisory','mckinsey','bcg','bain','deloitte','pwc','ey','kpmg'] },
    };

    // Build active chips from profile + observed jobs.
    function _buildChips() {
        const profile = (window.__dashboardData || {}).profile || {};
        const skills = (profile.primary_skills || []).map(s => String(s).toLowerCase());
        const domain = String(profile.domain || '').toLowerCase().replace(/\s+/g, '-');
        const level  = String(profile.experience_level || '').toLowerCase();
        const allJobs = jobsData || (window.__dashboardData || {}).jobs || [];

        const chips = [
            { id: 'all', label: 'All', test: () => true }
        ];

        // Domain chips: include cluster if profile.domain matches OR primary_skills contains any cluster keyword.
        const seen = new Set();
        for (const [key, cluster] of Object.entries(DOMAIN_CLUSTERS)) {
            if (seen.has(key)) continue;
            const domainMatch = domain && (domain === key || domain.includes(key));
            const skillMatch = skills.some(s => cluster.kw.some(k => s.includes(k)));
            if (!domainMatch && !skillMatch) continue;
            seen.add(key);
            chips.push({
                id: 'domain-' + key,
                label: cluster.label,
                test: row => {
                    const haystack = (row.dataset.skills || '') + ' ' + (row.cells?.[1]?.textContent || '').toLowerCase();
                    return cluster.kw.some(k => haystack.includes(k));
                },
            });
        }

        // Seniority chip — show only if profile is senior+ (otherwise meaningless).
        if (level === 'staff' || level === 'principal') {
            chips.push({
                id: 'staff', label: 'Staff+',
                test: row => /(staff|principal|distinguished)/i.test(row.dataset.seniority || ''),
            });
        } else if (level === 'senior') {
            chips.push({
                id: 'senior', label: 'Senior+',
                test: row => /(senior|staff|principal|lead)/i.test(row.dataset.seniority || ''),
            });
        }

        // Contract chip — only if any contract jobs exist.
        if (allJobs.some(j => /(contract|freelance)/i.test(String(j.job_type || '')))) {
            chips.push({
                id: 'contract', label: 'Contract',
                test: row => /(contract|freelance)/i.test(row.dataset.type || ''),
            });
        }

        // High-match chip — only if any job ≥60.
        if (allJobs.some(j => Number(j.match_score || 0) >= 60)) {
            chips.push({
                id: 'high-match', label: 'High Match (60%+)',
                test: row => parseFloat(row.dataset.match || 0) >= 60,
            });
        }

        return chips;
    }

    let _activeChips = [];
    let _activeChipId = 'all';

    function _renderChips() {
        const container = document.getElementById('filterChips');
        if (!container) return;
        _activeChips = _buildChips();
        container.innerHTML = '';
        _activeChips.forEach(chip => {
            const btn = document.createElement('button');
            btn.className = 'filter-btn' + (chip.id === _activeChipId ? ' active' : '');
            btn.textContent = chip.label;
            btn.onclick = () => filterJobs(chip.id, btn);
            container.appendChild(btn);
        });
    }

    function filterJobs(id, btn) {
        _activeChipId = id;
        document.querySelectorAll('#filterChips .filter-btn').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        const chip = _activeChips.find(c => c.id === id) || _activeChips[0];
        document.querySelectorAll('.job-row').forEach((row, i) => {
            const expandRow = document.getElementById('expand-' + (i + 1));
            const show = !!chip && chip.test(row);
            row.style.display = show ? '' : 'none';
            if (expandRow) { expandRow.style.display = show ? '' : 'none'; if (!show) expandRow.classList.remove('visible'); }
        });
    }

    function _bindSearch() {
        const input = document.getElementById('searchInput');
        if (!input) return;
        input.addEventListener('input', e => {
            const q = e.target.value.toLowerCase();
            document.querySelectorAll('.job-row').forEach((row, i) => {
                const text = row.textContent.toLowerCase();
                const show = !q || text.includes(q);
                row.style.display = show ? '' : 'none';
                const er = document.getElementById('expand-' + (i + 1));
                if (er && !show) er.classList.remove('visible');
            });
        });
    }

    let _sortDir = {};
    function sortTable(col) {
        const tbody = document.getElementById('jobsTable').querySelector('tbody');
        const pairs = []; const all = tbody.querySelectorAll('tr');
        for (let i = 0; i < all.length; i += 2) pairs.push([all[i], all[i+1]]);
        _sortDir[col] = !_sortDir[col];
        pairs.sort((a, b) => {
            const av = a[0].cells[col]?.textContent.trim() || '', bv = b[0].cells[col]?.textContent.trim() || '';
            const an = parseFloat(av.replace(/[^0-9.\-]/g, '')), bn = parseFloat(bv.replace(/[^0-9.\-]/g, ''));
            if (!isNaN(an) && !isNaN(bn)) return _sortDir[col] ? an - bn : bn - an;
            return _sortDir[col] ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        pairs.forEach(p => { tbody.appendChild(p[0]); tbody.appendChild(p[1]); });
    }

    // ── Document Generation Modal ──

    let _currentJobIdx = null, _cachedResume = {}, _cachedCover = {}, _currentTab = 'resume';

    function openModal(title) {
        document.getElementById('modalTitle').textContent = title;
        document.getElementById('docModal').classList.add('visible');
        document.getElementById('docLoading').style.display = 'block';
        document.getElementById('docResult').style.display = 'none';
    }
    function closeModal() { document.getElementById('docModal').classList.remove('visible'); }

    function switchTab(tab) {
        _currentTab = tab;
        document.getElementById('tabResume').classList.toggle('active', tab === 'resume');
        document.getElementById('tabCover').classList.toggle('active', tab === 'cover');
        if (tab === 'resume' && _cachedResume[_currentJobIdx]) _renderDoc(_cachedResume[_currentJobIdx]);
        else if (tab === 'cover' && _cachedCover[_currentJobIdx]) _renderDoc(_cachedCover[_currentJobIdx]);
        else if (tab === 'resume') generateResume(_currentJobIdx);
        else generateCover(_currentJobIdx);
    }

    async function generateResume(idx) {
        _currentJobIdx = idx; _currentTab = 'resume';
        document.getElementById('tabResume').classList.add('active');
        document.getElementById('tabCover').classList.remove('active');
        const job = jobsData[idx];
        openModal('Tailored Resume — ' + job.job_title + ' @ ' + job.company);
        if (_cachedResume[idx]) { _renderDoc(_cachedResume[idx]); return; }
        const data = await Api.generateResume({
            job_title: job.job_title, company: job.company,
            description: job.skill_matches.join(' ') + ' ' + (job.strengths || []).join(' ') + ' ' + job.job_type + ' ' + job.location
        });
        _cachedResume[idx] = data;
        _renderDoc(data);
    }

    async function generateCover(idx) {
        _currentJobIdx = idx; _currentTab = 'cover';
        document.getElementById('tabCover').classList.add('active');
        document.getElementById('tabResume').classList.remove('active');
        const job = jobsData[idx];
        openModal('Cover Letter — ' + job.job_title + ' @ ' + job.company);
        if (_cachedCover[idx]) { _renderDoc(_cachedCover[idx]); return; }
        const data = await Api.generateCover({
            job_title: job.job_title, company: job.company,
            description: job.skill_matches.join(' ') + ' ' + (job.strengths || []).join(' ') + ' ' + job.job_type + ' ' + job.location,
            tailored_resume: _cachedResume[idx]?.content || ''
        });
        _cachedCover[idx] = data;
        _renderDoc(data);
    }

    function _renderDoc(data) {
        document.getElementById('docLoading').style.display = 'none';
        document.getElementById('docResult').style.display = 'block';
        document.getElementById('docScore').textContent = data.score.toFixed(1);
        document.getElementById('docIters').textContent = data.total_iterations;

        const isResume = data.document_type === 'resume';
        document.getElementById('resumeContainer').style.display = isResume ? 'block' : 'none';
        document.getElementById('coverContainer').style.display  = isResume ? 'none'  : 'block';

        if (isResume) {
            const profile = (window.__dashboardData || {}).profile || {};
            document.getElementById('resumeFrame').srcdoc = ResumeBuilder.build(data.content, profile);
        } else {
            document.getElementById('coverContent').textContent = data.content;
        }

        // Score timeline
        const timeline = document.getElementById('scoreTimeline'); timeline.innerHTML = '';
        data.iterations.forEach(it => {
            const bar = document.createElement('div'); bar.className = 'score-bar-mini';
            bar.style.height = (it.score * 0.4) + 'px';
            bar.style.background = it.passed ? '#00cec9' : it.score > 50 ? '#fdcb6e' : '#e17055';
            bar.title = 'Iter ' + it.iteration + ': ' + it.score;
            timeline.appendChild(bar);
        });

        // Iteration timeline
        const iterDiv = document.getElementById('iterTimeline');
        iterDiv.innerHTML = '<h4>Writer-Reviewer Iterations</h4>';
        data.iterations.forEach(it => {
            const item = document.createElement('div'); item.className = 'iteration-item';
            item.innerHTML = `<div class="iter-badge ${it.passed ? 'iter-pass' : 'iter-fail'}">${it.iteration}</div>
            <div class="iter-details">
                <div class="score" style="color:${it.passed ? 'var(--green)' : it.score > 50 ? 'var(--orange)' : 'var(--red)'}">Score: ${it.score} ${it.passed ? '&#x2713; Passed' : '&#x2717; Needs refinement'}</div>
                <div class="items">${it.strengths.length ? '<span style="color:var(--green)">&#x2713; ' + it.strengths.join('</span> <span style="color:var(--green)">&#x2713; ') + '</span>' : ''} ${it.weaknesses.length ? '<span style="color:var(--orange)">&#x26A0; ' + it.weaknesses.join('</span> <span style="color:var(--orange)">&#x26A0; ') + '</span>' : ''}</div>
                ${it.suggestions.length ? '<div class="items" style="margin-top:2px;">Suggestions: ' + it.suggestions.join('; ') + '</div>' : ''}
            </div>`;
            iterDiv.appendChild(item);
        });
    }

    function printDoc() {
        if (_currentTab === 'resume') {
            const iframe = document.getElementById('resumeFrame');
            iframe.contentWindow.focus();
            iframe.contentWindow.print();
        } else {
            const content = document.getElementById('coverContent').textContent;
            const w = window.open('', '', 'width=800,height=600');
            w.document.write(`<!DOCTYPE html><html><head><style>body{font-family:Georgia,serif;max-width:700px;margin:40px auto;padding:20px;line-height:1.8;color:#222;font-size:14px;}@media print{@page{margin:1in;}}</style></head><body><pre style="white-space:pre-wrap;font-family:Georgia,serif;">${content}</pre></body></html>`);
            w.document.close();
            setTimeout(() => { w.focus(); w.print(); }, 300);
        }
    }

    function copyDoc() {
        let text;
        if (_currentTab === 'resume') {
            text = document.getElementById('resumeFrame').contentDocument.body.innerText;
        } else {
            text = document.getElementById('coverContent').textContent;
        }
        navigator.clipboard.writeText(text).then(() => {
            const btn = document.querySelector('.copy-btn');
            const orig = btn.innerHTML;
            btn.innerHTML = '&#x2713; Copied!';
            setTimeout(() => btn.innerHTML = orig, 2000);
        });
    }

    return {
        init, switchProvider, triggerRefresh, cancelRefresh,
        toggleExpand, filterJobs, sortTable,
        generateResume, generateCover, switchTab, closeModal, printDoc, copyDoc,
        // exposed for tests
        _buildChips, DOMAIN_CLUSTERS,
    };
})();

if (typeof document !== 'undefined' && document.addEventListener && typeof window !== 'undefined' && !window.__JH_TEST__) {
    document.addEventListener('DOMContentLoaded', () => {
        DashboardVM.init();
        SourcesVM.init();
        const modal = document.getElementById('docModal');
        if (modal) modal.addEventListener('click', e => { if (e.target === modal) DashboardVM.closeModal(); });
        const srcModal = document.getElementById('addSourceModal');
        if (srcModal) srcModal.addEventListener('click', e => { if (e.target === srcModal) SourcesVM.closeAddModal(); });
    });
}

/**
 * Sources ViewModel
 * Manages custom job source CRUD and renders the sources list.
 */
const SourcesVM = (() => {

    async function init() {
        await _render();
    }

    function openAddModal() {
        document.getElementById('srcName').value = '';
        document.getElementById('srcUrl').value = '';
        document.getElementById('srcType').value = 'rss';
        document.getElementById('srcQuality').value = '60';
        document.getElementById('addSourceError').style.display = 'none';
        const m = document.getElementById('addSourceModal');
        m.style.display = 'flex';
    }

    function closeAddModal() {
        document.getElementById('addSourceModal').style.display = 'none';
    }

    async function addSource() {
        const name    = document.getElementById('srcName').value.trim();
        const url     = document.getElementById('srcUrl').value.trim();
        const type    = document.getElementById('srcType').value;
        const quality = parseInt(document.getElementById('srcQuality').value) || 60;
        const errEl   = document.getElementById('addSourceError');

        if (!name || !url) {
            errEl.textContent = 'Name and URL are required.';
            errEl.style.display = 'block';
            return;
        }

        const data = await Api.addSource({ name, url, source_type: type, quality });
        if (data.ok) {
            closeAddModal();
            await _render();
        } else {
            errEl.textContent = data.message || 'Failed to add source.';
            errEl.style.display = 'block';
        }
    }

    async function removeSource(name) {
        if (!confirm(`Remove source "${name}"?`)) return;
        await Api.removeSource(name);
        await _render();
    }

    async function toggleSource(name, enabled) {
        await Api.toggleSource(name, enabled);
        await _render();
    }

    async function _render() {
        const container = document.getElementById('customSourcesList');
        if (!container) return;

        const data = await Api.getSources();
        const custom = data.custom || [];
        const builtin = data.builtin || [];

        if (custom.length === 0) {
            container.innerHTML = `
                <div style="padding: 20px; background: var(--surface); border: 1px dashed var(--border); border-radius: var(--radius); text-align: center; color: var(--text2); font-size: 13px;">
                    No custom sources added yet. Click <strong>+ Add Source</strong> to add an RSS feed or JSON API.
                    <div style="margin-top: 8px; font-size: 11px; opacity: 0.7;">26 built-in sources are always active (LinkedIn, Remotive, WeWorkRemotely, etc.)</div>
                </div>`;
            return;
        }

        const rows = custom.map(s => {
            const qColor = s.quality >= 80 ? 'var(--green)' : s.quality >= 60 ? 'var(--accent2)' : 'var(--orange)';
            return `
            <div style="display:flex; align-items:center; gap:14px; padding:12px 16px; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); margin-bottom:8px;">
                <div style="width:40px; height:40px; border-radius:8px; background:rgba(108,92,231,0.15); display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:700; color:${qColor}; flex-shrink:0;">${s.quality}</div>
                <div style="flex:1; min-width:0;">
                    <div style="font-size:14px; font-weight:600;">${s.name}</div>
                    <div style="font-size:11px; color:var(--text2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${s.url} &middot; ${s.source_type}</div>
                </div>
                <label style="display:flex; align-items:center; gap:6px; cursor:pointer; font-size:12px; color:var(--text2); flex-shrink:0;">
                    <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="SourcesVM.toggleSource('${s.name}', this.checked)" style="accent-color:var(--accent);">
                    Active
                </label>
                <button onclick="SourcesVM.removeSource('${s.name}')" title="Remove" style="padding:6px 10px; background:transparent; border:1px solid var(--border); border-radius:6px; color:var(--red); cursor:pointer; font-size:12px; flex-shrink:0;">✕ Remove</button>
            </div>`;
        }).join('');

        container.innerHTML = rows;
    }

    return { init, openAddModal, closeAddModal, addSource, removeSource, toggleSource };
})();

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { DashboardVM, SourcesVM };
}
