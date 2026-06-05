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

    function filterJobs(type, btn) {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.job-row').forEach((row, i) => {
            const expandRow = document.getElementById('expand-' + (i + 1));
            const jt = row.dataset.type || '', match = parseFloat(row.dataset.match || 0);
            const skills = row.dataset.skills || '', seniority = row.dataset.seniority || '';
            let show = true;
            if (type === 'contract')   show = jt.includes('contract') || jt.includes('freelance');
            else if (type === 'genai') show = skills.includes('genai') || skills.includes('llm') || skills.includes('ai') || skills.includes('machine learning');
            else if (type === 'mobile') show = skills.includes('android') || skills.includes('mobile') || skills.includes('kotlin') || skills.includes('flutter');
            else if (type === 'staff') show = seniority.includes('Staff') || seniority.includes('Principal');
            else if (type === 'high-match') show = match >= 60;
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
