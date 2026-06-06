/**
 * Onboarding ViewModel
 * Manages: file upload, role input, provider selection,
 * pipeline progress with cancel, and API key setup.
 *
 * Expects the View to set window.__serverState = { pipelineRunning, pipelineStatus }
 * before this script loads.
 */
const OnboardingVM = (() => {
    // ── DOM refs ──
    let $form, $uploadArea, $fileInput, $fileName, $roleInput, $launchBtn;
    let $progressSection, $progressFill, $progressStatus, $cancelBtn;
    let $sourceTicker, $sourceChips;

    // ── Pipeline stages for connector UI ──
    const STAGES = [
        { id: 'parse',   active: 5,  done: 15 },
        { id: 'refine',  active: 15, done: 25 },
        { id: 'scrape',  active: 25, done: 50 },
        { id: 'company', active: 50, done: 70 },
        { id: 'match',   active: 70, done: 90 },
    ];

    let _pollInterval = null;

    // ── Init ──

    function init() {
        $form            = document.getElementById('searchForm');
        $uploadArea      = document.getElementById('uploadArea');
        $fileInput       = document.getElementById('resumeFile');
        $fileName        = document.getElementById('fileName');
        $roleInput       = document.getElementById('roleInput');
        $launchBtn       = document.getElementById('launchBtn');
        $progressSection = document.getElementById('progressSection');
        $progressFill    = document.getElementById('progressFill');
        $progressStatus  = document.getElementById('progressStatus');
        $cancelBtn       = document.getElementById('cancelBtn');
        $sourceTicker    = document.getElementById('sourceTicker');
        $sourceChips     = document.getElementById('sourceChips');

        _bindFileUpload();
        _bindRoleInput();
        _bindWorkMode();
        _bindProviderToggle();
        _renderProviders();
        _bindFormSubmit();

        // Resume if pipeline was already running (page refresh recovery)
        const state = window.__serverState || {};
        if (state.pipelineRunning) {
            _showProgress();
            if (state.pipelineStatus?.progress) $progressFill.style.width = state.pipelineStatus.progress + '%';
            if (state.pipelineStatus?.message) $progressStatus.textContent = state.pipelineStatus.message;
            _updateSteps(state.pipelineStatus?.progress || 0);
            _updateSourceTicker(state.pipelineStatus || {});
            _startPolling();
        }
    }

    // ── File Upload ──

    function _bindFileUpload() {
        $uploadArea.addEventListener('click', () => $fileInput.click());
        $uploadArea.addEventListener('dragover', e => { e.preventDefault(); $uploadArea.classList.add('dragover'); });
        $uploadArea.addEventListener('dragleave', () => $uploadArea.classList.remove('dragover'));
        $uploadArea.addEventListener('drop', e => {
            e.preventDefault();
            $uploadArea.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                $fileInput.files = e.dataTransfer.files;
                _onFileSelect();
            }
        });
        $fileInput.addEventListener('change', _onFileSelect);

        // Re-scan with the new depth when the deep-scan toggle changes.
        // Clear previously-selected roles so stale simple-scan picks don't
        // bleed into the deep-scan result.
        const $deep = document.getElementById('deepScan');
        if ($deep) {
            $deep.addEventListener('change', () => {
                const f = $fileInput.files[0];
                if (f) {
                    $roleInput.value = '';
                    _scanResume(f);
                }
            });
        }
    }

    function _onFileSelect() {
        if ($fileInput.files.length) {
            const f = $fileInput.files[0];
            $fileName.textContent = f.name + ' (' + (f.size / 1024).toFixed(0) + ' KB)';
            $uploadArea.classList.add('has-file');
            _checkReady();
            _scanResume(f);
        }
    }

    async function _scanResume(file) {
        const $suggestions = document.querySelector('.suggestions');
        const $scanStatus = document.getElementById('scanStatus');
        if (!$suggestions) return;

        const deep = !!(document.getElementById('deepScan') || {}).checked;

        // Show scanning state
        $suggestions.innerHTML = '<span style="color:var(--text2); font-size:12px; padding:6px;">&#x1f50d; '
            + (deep ? 'Deep scanning resume (~20s)...' : 'Scanning resume for role recommendations...')
            + '</span>';

        try {
            const fd = new FormData();
            fd.append('resume', file);
            fd.append('mode', deep ? 'deep' : 'quick');
            const data = await Api.scanResume(fd);

            if (!data.ok) {
                $suggestions.innerHTML = _defaultChips();
                if ($scanStatus) {
                    $scanStatus.style.display = 'block';
                    $scanStatus.innerHTML = '<span style="color:var(--red); font-size:12px;">&#x26a0; '
                        + (data.message || 'Scan failed') + '</span>';
                }
                return;
            }

            // Show experience summary above suggestions
            if ($scanStatus && data.summary) {
                $scanStatus.style.display = 'block';
                $scanStatus.innerHTML = '<span style="color:var(--green); font-size:12px;">&#x2713; ' + data.summary + '</span>';
                if (data.experience_years) {
                    $scanStatus.innerHTML += ' <span style="color:var(--accent2); font-size:11px; margin-left:6px;">'
                        + data.experience_years + '+ yrs &middot; ' + data.experience_level + '</span>';
                }
            }

            // Replace chips with personalized recommendations
            const roles = data.recommended_roles || [];
            if (roles.length > 0) {
                $suggestions.innerHTML = roles.map(role =>
                    '<span class="suggestion-chip" onclick="addRole(this)">' + role + '</span>'
                ).join('');

                // Store seniority metadata for search hardening
                window.__resumeScan = {
                    experience_years: data.experience_years || 0,
                    experience_level: data.experience_level || 'senior',
                    seniority_keywords: data.seniority_keywords || [],
                    exclude_keywords: data.exclude_keywords || [],
                    domain: data.domain || '',
                };
            } else {
                $suggestions.innerHTML = _defaultChips();
            }
        } catch (err) {
            console.error('Resume scan failed:', err);
            $suggestions.innerHTML = _defaultChips();
        }
    }

    function _defaultChips() {
        // No-op fallback. We deliberately do NOT seed engineering roles here:
        // showing "Staff Android Engineer" to a Chartered Accountant biased the
        // entire pipeline because users would tap a chip rather than type.
        // When the scan can't extract roles, ask the user to type their own.
        return '<span style="color:var(--text2); font-size:12px; padding:6px;">'
            + 'No role suggestions could be derived from this resume. '
            + 'Type the roles you want to target above.</span>';
    }

    // ── Role Input ──

    function _bindRoleInput() {
        $roleInput.addEventListener('input', _checkReady);
    }

    function addRole(chip) {
        const current = $roleInput.value.split(',').map(r => r.trim()).filter(Boolean);
        const newRole = chip.textContent.trim();
        if (!current.includes(newRole)) {
            current.push(newRole);
            $roleInput.value = current.join(', ');
        }
        chip.style.opacity = '0.4';
        _checkReady();
    }

    function _checkReady() {
        const roles = $roleInput.value.split(',').map(r => r.trim()).filter(r => r.length > 2);
        $launchBtn.disabled = !($fileInput.files.length && roles.length > 0);
    }

    // ── Work Mode ──

    function _bindWorkMode() {
        const $hidden = document.getElementById('workModeInput');
        const $locInput = document.getElementById('locationInput');
        const toggles = document.querySelectorAll('#workModeGroup .mode-toggle');

        function _getActiveModes() {
            return [...document.querySelectorAll('#workModeGroup .mode-toggle.active')].map(t => t.dataset.value);
        }

        function syncHidden() {
            const active = _getActiveModes();
            $hidden.value = active.length ? active.join(',') : 'any';
            _enforceLocationForOnsite(active);
        }

        // When on-site is selected, "Anywhere" is invalid — set to resume
        // location or clear so user must type one.
        function _enforceLocationForOnsite(modes) {
            if (!$locInput) return;
            const hasOnsite = modes.includes('onsite');
            const isAnywhere = ($locInput.value || '').trim().toLowerCase() === 'anywhere'
                            || $locInput.value.trim() === '';

            if (hasOnsite && isAnywhere) {
                // Try resume scan location first.
                const scanLoc = (window.__resumeScan || {}).location
                    || ((window.__dashboardData || {}).profile || {}).location
                    || '';
                if (scanLoc && scanLoc.toLowerCase() !== 'anywhere') {
                    $locInput.value = scanLoc;
                } else {
                    $locInput.value = '';
                    $locInput.placeholder = 'Required for on-site — enter city or region';
                }
                $locInput.focus();
            } else if (!hasOnsite) {
                // Restore default placeholder when on-site removed.
                $locInput.placeholder = 'e.g. Anywhere, US, Europe, San Francisco';
            }
        }

        toggles.forEach(toggle => {
            toggle.addEventListener('click', () => {
                const val = toggle.dataset.value;
                if (val === 'any') {
                    // "Any" clears all others
                    toggles.forEach(t => t.classList.remove('active'));
                    toggle.classList.add('active');
                } else {
                    // Remove "Any" if present
                    document.querySelector('#workModeGroup .mode-toggle[data-value="any"]')
                        ?.classList.remove('active');
                    // Toggle this one
                    toggle.classList.toggle('active');
                    // If nothing selected, activate "Any"
                    const anyActive = [...toggles].some(t => t.classList.contains('active'));
                    if (!anyActive) {
                        document.querySelector('#workModeGroup .mode-toggle[data-value="any"]')
                            .classList.add('active');
                    }
                }
                syncHidden();
            });
        });
    }

    // ── LLM Provider (dynamic from /api/providers) ──

    function _bindProviderToggle() { /* dynamic — buttons created by _renderProviders */ }

    async function _renderProviders() {
        const wrap = document.getElementById('providerToggle');
        const hidden = document.getElementById('providerInput');
        const status = document.getElementById('providerStatus');
        if (!wrap) return;
        try {
            const d = await Api.getProviders();
            wrap.innerHTML = '';
            const list = d.providers || [];
            // Pick active: server says, else first available
            let active = d.active;
            if (!list.some(p => p.id === active && p.available)) {
                const firstOk = list.find(p => p.available);
                if (firstOk) active = firstOk.id;
            }
            list.forEach(p => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'provider-btn' + (p.id === active ? ' active' : '');
                btn.dataset.provider = p.id;
                btn.title = p.detail || '';
                btn.style.opacity = p.available ? '1' : '0.45';
                btn.disabled = !p.available;
                btn.innerHTML = `${p.icon} ${p.label} <span style="font-size:10px; opacity:0.6;">(${p.kind})</span>`;
                btn.onclick = () => setProvider(btn);
                wrap.appendChild(btn);
            });
            hidden.value = active;
            if (active) {
                try { await Api.setProvider(active); } catch {}
                const ap = list.find(p => p.id === active);
                if (ap && status) {
                    status.textContent = ap.detail || '';
                    status.style.color = 'var(--green)';
                }
            } else if (status) {
                status.textContent = 'No provider configured — run install.sh';
                status.style.color = 'var(--red)';
            }
        } catch (e) { console.error('providers fetch failed', e); }
    }

    async function setProvider(btn) {
        document.querySelectorAll('.provider-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const id = btn.dataset.provider;
        document.getElementById('providerInput').value = id;
        const status = document.getElementById('providerStatus');
        try {
            const r = await Api.setProvider(id);
            if (status) {
                status.textContent = r.ok ? `Active: ${id}` : (r.message || 'Switch failed');
                status.style.color = r.ok ? 'var(--green)' : 'var(--red)';
            }
        } catch {
            if (status) { status.textContent = 'Cannot reach server'; status.style.color = 'var(--red)'; }
        }
        // Re-scan with the new provider if a resume is already uploaded.
        // Clears stale error banners from a previous provider failure.
        if ($fileInput && $fileInput.files.length) {
            _scanResume($fileInput.files[0]);
        }
    }

    // ── Form Submit ──

    function _bindFormSubmit() {
        $form.addEventListener('submit', async (e) => {
            e.preventDefault();

            // Validate: on-site requires a real location.
            const modes = (document.getElementById('workModeInput').value || '').split(',');
            const loc = (document.getElementById('locationInput') || {}).value || '';
            if (modes.includes('onsite') && (!loc.trim() || loc.trim().toLowerCase() === 'anywhere')) {
                alert('On-site mode requires a specific location. Please enter a city or region.');
                document.getElementById('locationInput').focus();
                return;
            }

            _showProgress();
            _resetSteps();
            $progressFill.style.width = '0%';
            $progressStatus.textContent = 'Initializing...';

            try {
                const data = await Api.startSearch(new FormData($form));
                if (data.status === 'started' || data.status === 'already_running') {
                    _startPolling();
                } else {
                    $progressStatus.textContent = 'Error: ' + (data.message || 'Unknown');
                }
            } catch (err) {
                $progressStatus.textContent = 'Error: ' + err.message;
            }
        });
    }

    // ── Progress / Pipeline ──

    let _verbose = false;

    function _showProgress() {
        $form.style.display = 'none';
        $progressSection.classList.add('active');
        $cancelBtn.style.display = 'inline-block';
        // Remove any leftover retry button
        const old = document.getElementById('retryBtn');
        if (old) old.remove();
    }

    function _hideProgress() {
        $cancelBtn.style.display = 'none';
    }

    function _startPolling() {
        if (_pollInterval) clearInterval(_pollInterval);
        _pollInterval = setInterval(async () => {
            try {
                const data = await Api.searchStatus();
                $progressFill.style.width = data.progress + '%';
                $progressStatus.innerHTML = data.message;
                _updateSteps(data.progress, data.message);
                _updateSourceTicker(data);

                if (!data.running && data.progress >= 100) {
                    clearInterval(_pollInterval);
                    _hideProgress();
                    setTimeout(() => { window.location.href = '/dashboard'; }, 500);
                }
                if (!data.running && data.progress === 0) {
                    clearInterval(_pollInterval);
                    _hideProgress();
                    $progressStatus.innerHTML = '<span style="color:var(--red);">' + data.message + '</span>';
                    _showRetryButton();
                }
            } catch { /* retry next tick */ }
        }, 1500);
    }

    function _showRetryButton() {
        if (document.getElementById('retryBtn')) return;
        const btn = document.createElement('button');
        btn.id = 'retryBtn';
        btn.textContent = '↻ Retry Pipeline';
        btn.style.cssText = 'margin-top:14px; padding:8px 20px; border-radius:8px; border:1px solid var(--accent2); background:transparent; color:var(--accent2); cursor:pointer; font-size:13px; font-weight:600;';
        btn.onclick = () => {
            btn.remove();
            // Re-submit the form to restart the pipeline
            $form.style.display = 'block';
            $progressSection.classList.remove('active');
            _resetSteps();
        };
        $progressStatus.parentElement.appendChild(btn);
    }

    async function cancelPipeline() {
        $cancelBtn.disabled = true;
        $cancelBtn.style.opacity = '0.4';
        try {
            const r = await Api.cancelPipeline();
            // If user was removed (no results yet), redirect to fresh start
            if (r && r.removed) {
                clearInterval(_pollInterval);
                window.location.href = '/start?new=1';
                return;
            }
        } catch { /* polling catches it */ }
    }

    // ── Pipeline Step Connectors ──

    // ── Source Ticker ──

    function _updateSourceTicker(data) {
        const isScraping = data.progress >= 25 && data.progress < 50;

        if (!isScraping) {
            // Hide ticker outside scrape step — but keep chips visible if scrape is done
            if (data.progress >= 50 && $sourceChips.children.length > 0) {
                $sourceTicker.style.display = 'block'; // keep visible as summary
            } else if (data.progress < 25) {
                $sourceTicker.style.display = 'none';
                $sourceChips.innerHTML = '';
            }
            return;
        }

        $sourceTicker.style.display = 'block';

        const done   = data.scraped_sources || [];
        const active = data.current_source || '';

        // Build chip set: completed + active
        $sourceChips.innerHTML = done.map(s => `
            <span style="
                display:inline-flex; align-items:center; gap:4px;
                padding:3px 9px; border-radius:20px; font-size:11px; font-weight:500;
                background:${s.ok ? 'rgba(0,206,201,0.12)' : 'rgba(225,112,85,0.12)'};
                color:${s.ok ? 'var(--green)' : 'var(--red)'};
                border:1px solid ${s.ok ? 'rgba(0,206,201,0.3)' : 'rgba(225,112,85,0.3)'};
                white-space:nowrap;
            ">
                ${s.ok ? '✓' : '✗'} ${s.name}${s.count ? ' <span style="opacity:0.6">('+s.count+')</span>' : ''}
            </span>`
        ).join('') + (active ? `
            <span style="
                display:inline-flex; align-items:center; gap:5px;
                padding:3px 9px; border-radius:20px; font-size:11px; font-weight:600;
                background:rgba(108,92,231,0.18); color:var(--accent2);
                border:1px solid rgba(108,92,231,0.4);
                white-space:nowrap;
            ">
                <span style="width:6px;height:6px;border-radius:50%;background:var(--accent2);display:inline-block;animation:pulse 1s ease-in-out infinite;"></span>
                ${active}
            </span>` : '');
    }

    function _resetSteps() {
        STAGES.forEach(s => {
            const el = document.getElementById('pipe-' + s.id);
            el.classList.remove('done', 'active');
            const det = el.querySelector('.step-detail');
            if (det) det.textContent = '';
        });
        [1, 2, 3, 4].forEach(i => {
            document.getElementById('conn-' + i).classList.remove('done', 'active');
        });
    }

    function _updateSteps(progress, statusMsg) {
        STAGES.forEach((s, i) => {
            const el = document.getElementById('pipe-' + s.id);
            if (progress >= s.done)       { el.classList.add('done'); el.classList.remove('active'); }
            else if (progress >= s.active) { el.classList.add('active'); el.classList.remove('done'); }
            else                           { el.classList.remove('done', 'active'); }

            // Verbose detail: show server message under the currently active step
            let det = el.querySelector('.step-detail');
            if (!det) {
                det = document.createElement('div');
                det.className = 'step-detail';
                el.appendChild(det);
            }
            if (el.classList.contains('active') && _verbose && statusMsg) {
                det.textContent = statusMsg;
                det.style.display = 'block';
            } else if (el.classList.contains('done') && _verbose) {
                det.textContent = '✓ done';
                det.style.display = 'block';
            } else {
                det.style.display = 'none';
            }
        });
        for (let i = 1; i <= 4; i++) {
            const conn = document.getElementById('conn-' + i);
            const prev = document.getElementById('pipe-' + STAGES[i-1].id);
            const next = document.getElementById('pipe-' + STAGES[i].id);
            if (prev.classList.contains('done') && (next.classList.contains('done') || next.classList.contains('active'))) {
                conn.classList.add('done'); conn.classList.remove('active');
            } else if (prev.classList.contains('done') || prev.classList.contains('active')) {
                conn.classList.add('active'); conn.classList.remove('done');
            } else {
                conn.classList.remove('done', 'active');
            }
        }
    }

    function toggleVerbose() {
        _verbose = !_verbose;
        const btn = document.getElementById('verboseBtn');
        if (btn) {
            btn.classList.toggle('active', _verbose);
            btn.title = _verbose ? 'Hide step details' : 'Show step details';
        }
        // Immediately refresh step details
        const data = { progress: parseFloat($progressFill.style.width) || 0, message: $progressStatus.textContent };
        _updateSteps(data.progress, data.message);
    }

    // ── API Key ──

    async function saveApiKey() {
        const key = document.getElementById('apiKeyInput').value.trim();
        const status = document.getElementById('apiStatus');
        if (!key) { status.textContent = 'Please enter a key'; status.className = 'api-status err'; return; }
        const data = await Api.setApiKey(key);
        status.textContent = data.message;
        status.className = 'api-status ' + (data.ok ? 'ok' : 'err');
    }

    async function saveGroqKey() {
        const key = document.getElementById('groqKeyInput').value.trim();
        const status = document.getElementById('groqStatus');
        if (!key) { status.textContent = 'Please enter a key'; status.className = 'api-status err'; return; }
        const data = await Api.setGroqKey(key);
        status.textContent = data.ok ? 'Groq key saved' : (data.message || 'Failed');
        status.className = 'api-status ' + (data.ok ? 'ok' : 'err');
        if (data.ok) _renderProviders();
    }

    return { init, addRole, setProvider, cancelPipeline, toggleVerbose, saveApiKey, saveGroqKey };
})();

if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', () => OnboardingVM.init());
}
if (typeof module !== 'undefined' && module.exports) { module.exports = OnboardingVM; }
