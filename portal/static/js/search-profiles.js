/**
 * Search Profiles ViewModel
 * Manages search configurations in the burger menu panel.
 * Each config = roles + work mode + scan depth + location + salary + seniority + excludes.
 */
const SearchProfiles = (() => {
    let $list, $count, $modal;

    function init() {
        $list  = document.getElementById('searchProfileList');
        $count = document.getElementById('spCount');
        $modal = document.getElementById('spModal');
    }

    // ── Load + Render ──

    async function load() {
        if (!$list) return;
        try {
            const data = await Api.getSearchProfiles();
            const profiles = data.profiles || [];
            const activeId = data.active_id;
            $count.textContent = profiles.length + ' config' + (profiles.length !== 1 ? 's' : '');

            if (!profiles.length) {
                $list.innerHTML = '<div style="text-align:center; color:var(--text2); padding:12px; font-size:12px;">No search configs yet.</div>';
                return;
            }

            $list.innerHTML = '';
            profiles.forEach(p => {
                const isActive = p.id === activeId;
                const card = document.createElement('div');
                card.className = 'sp-card' + (isActive ? ' active' : '');
                card.onclick = () => switchProfile(p.id);

                // Build chips
                let chips = '';
                if (p.roles && p.roles.length) {
                    const shown = p.roles.slice(0, 2).map(r => `<span class="sp-chip">${r}</span>`).join('');
                    const more = p.roles.length > 2 ? `<span class="sp-chip">+${p.roles.length - 2}</span>` : '';
                    chips += shown + more;
                }
                chips += `<span class="sp-chip mode">${_modeIcon(p.work_mode)} ${p.work_mode}</span>`;
                if (p.location && p.location !== 'Global') {
                    chips += `<span class="sp-chip loc">${p.location}</span>`;
                }
                if (p.scan_depth === 'deep') {
                    chips += '<span class="sp-chip" style="background:rgba(253,203,110,0.15); color:var(--orange);">Deep Scan</span>';
                }
                if (p.salary_floor) {
                    chips += `<span class="sp-chip loc">$${(p.salary_floor / 1000).toFixed(0)}k+</span>`;
                }
                if (p.seniority_override) {
                    chips += `<span class="sp-chip">${p.seniority_override}</span>`;
                }
                if (p.exclude_keywords && p.exclude_keywords.length) {
                    chips += `<span class="sp-chip" style="background:rgba(255,107,107,0.12); color:var(--red);">-${p.exclude_keywords.length} excl</span>`;
                }

                let badges = '';
                if (isActive) badges += '<span class="user-badge badge-active" style="font-size:9px;">Active</span> ';
                if (p.has_results) badges += `<span class="user-badge badge-results" style="font-size:9px;">${p.job_count} jobs</span> `;

                card.innerHTML = `
                    <div class="sp-name">${_esc(p.name)}</div>
                    <div class="sp-chips">${chips}</div>
                    <div style="margin-top:4px;">${badges}</div>
                    <div class="sp-actions">
                        <button class="sp-action-btn" onclick="event.stopPropagation(); SearchProfiles.showEdit('${p.id}')" title="Edit">&#x270e;</button>
                        <button class="sp-action-btn delete" onclick="event.stopPropagation(); SearchProfiles.remove('${p.id}', '${_esc(p.name)}')" title="Delete">&times;</button>
                    </div>
                `;
                $list.appendChild(card);
            });
        } catch (err) {
            console.error('Failed to load search profiles:', err);
        }
    }

    // ── Actions ──

    async function switchProfile(profileId) {
        const data = await Api.switchSearchProfile(profileId);
        if (data.ok) window.location.reload();
    }

    async function remove(profileId, name) {
        if (!confirm(`Delete search config "${name}"?`)) return;
        const data = await Api.deleteSearchProfile(profileId);
        if (data.ok) load();
    }

    // ── Modal ──

    function showCreate() {
        document.getElementById('spModalTitle').textContent = 'New Search Config';
        document.getElementById('spEditId').value = '';
        document.getElementById('spName').value = '';
        document.getElementById('spRoles').value = '';
        document.getElementById('spWorkMode').value = 'remote';
        document.getElementById('spScanDepth').value = 'quick';
        document.getElementById('spLocation').value = 'Global';
        document.getElementById('spSalary').value = '';
        document.getElementById('spSeniority').value = '';
        document.getElementById('spExclude').value = '';

        // Pre-fill from resume scan if available
        if (window.__resumeScan) {
            const scan = window.__resumeScan;
            if (scan.exclude_keywords) {
                document.getElementById('spExclude').value = scan.exclude_keywords.join(', ');
            }
        }

        $modal.style.display = 'flex';
    }

    async function showEdit(profileId) {
        const data = await Api.getSearchProfiles();
        const p = (data.profiles || []).find(x => x.id === profileId);
        if (!p) return;

        document.getElementById('spModalTitle').textContent = 'Edit Search Config';
        document.getElementById('spEditId').value = p.id;
        document.getElementById('spName').value = p.name || '';
        document.getElementById('spRoles').value = (p.roles || []).join(', ');
        document.getElementById('spWorkMode').value = p.work_mode || 'remote';
        document.getElementById('spScanDepth').value = p.scan_depth || 'quick';
        document.getElementById('spLocation').value = p.location || 'Global';
        document.getElementById('spSalary').value = p.salary_floor || '';
        document.getElementById('spSeniority').value = p.seniority_override || '';
        document.getElementById('spExclude').value = (p.exclude_keywords || []).join(', ');

        $modal.style.display = 'flex';
    }

    function hideModal() {
        $modal.style.display = 'none';
    }

    async function save() {
        const editId = document.getElementById('spEditId').value;
        const payload = {
            name: document.getElementById('spName').value.trim() || 'Untitled',
            roles: document.getElementById('spRoles').value.split(',').map(s => s.trim()).filter(Boolean),
            work_mode: document.getElementById('spWorkMode').value,
            scan_depth: document.getElementById('spScanDepth').value,
            location: document.getElementById('spLocation').value.trim() || 'Global',
            salary_floor: parseInt(document.getElementById('spSalary').value) || null,
            seniority_override: document.getElementById('spSeniority').value || null,
            exclude_keywords: document.getElementById('spExclude').value.split(',').map(s => s.trim()).filter(Boolean),
        };

        if (editId) {
            await Api.updateSearchProfile(editId, payload);
        } else {
            await Api.createSearchProfile(payload);
        }
        hideModal();
        load();
    }

    // ── Helpers ──

    function _modeIcon(mode) {
        if (mode === 'remote') return '&#x1f30d;';
        if (mode === 'hybrid') return '&#x1f3e2;';
        return '&#x1f4cd;';
    }

    function _esc(s) {
        return (s || '').replace(/'/g, "\\'").replace(/</g, '&lt;');
    }

    return { init, load, switchProfile, remove, showCreate, showEdit, hideModal, save, _modeIcon, _esc };
})();

if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', () => SearchProfiles.init());
}
if (typeof module !== 'undefined' && module.exports) { module.exports = SearchProfiles; }
