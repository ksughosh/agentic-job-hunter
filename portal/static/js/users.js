/**
 * User Panel ViewModel
 * Shared between onboarding and dashboard pages.
 * Manages the burger menu, user list, and profile switching.
 */
const UserPanel = (() => {
    // ── State ──
    let _isOpen = false;

    // ── DOM refs (bound on init) ──
    let $panel, $overlay, $btn, $list, $count;

    function init() {
        $panel  = document.getElementById('userPanel');
        $overlay = document.getElementById('panelOverlay');
        $btn    = document.getElementById('burgerBtn');
        $list   = document.getElementById('userList');
        $count  = document.getElementById('userCount');
    }

    // ── Actions ──

    function toggle() {
        _isOpen = !_isOpen;
        $panel.classList.toggle('open', _isOpen);
        $overlay.classList.toggle('visible', _isOpen);
        $btn.classList.toggle('open', _isOpen);
        if (_isOpen) {
            loadUsers();
            if (typeof SearchProfiles !== 'undefined') SearchProfiles.load();
        }
    }

    async function loadUsers() {
        const data = await Api.getUsers();
        const users = data.users;
        const currentId = data.current_user_id;
        $count.textContent = users.length + ' profile' + (users.length !== 1 ? 's' : '');
        $list.innerHTML = '';

        if (!users.length) {
            $list.innerHTML = '<div style="text-align:center; color:var(--text2); padding:20px; font-size:13px;">No profiles yet. Start by uploading your resume below.</div>';
            return;
        }

        users.forEach(u => {
            const isActive = u.id === currentId;
            const card = document.createElement('div');
            card.className = 'user-card' + (isActive ? ' active' : '');
            card.onclick = () => switchUser(u.id);

            let badges = '';
            if (isActive)         badges += '<span class="user-badge badge-active">Active</span> ';
            if (u.pipeline_running) badges += '<span class="user-badge badge-running">Running...</span> ';
            if (u.has_results)    badges += '<span class="user-badge badge-results">' + u.job_count + ' jobs</span> ';

            let roles = '';
            if (u.desired_roles && u.desired_roles.length) {
                roles = '<div style="font-size:11px; color:var(--text2); margin-top:4px;">' + u.desired_roles.join(', ') + '</div>';
            }

            card.innerHTML = `
                <div class="user-name">${u.name}</div>
                <div class="user-meta">Created ${new Date(u.created_at).toLocaleDateString()}</div>
                ${roles}
                <div>${badges}</div>
                <button class="user-delete" onclick="event.stopPropagation(); UserPanel.deleteUser('${u.id}', '${u.name}', ${u.has_results || u.pipeline_running})" title="Delete profile">&times;</button>
            `;
            $list.appendChild(card);
        });
    }

    async function switchUser(userId) {
        const data = await Api.switchUser(userId);
        if (data.ok) window.location.href = data.redirect;
    }

    async function addNewUser() {
        // Don't create a "New User" entry yet — just navigate to onboarding.
        // The user record is created lazily when the resume scan succeeds,
        // so the sidebar never shows a blank "New User" placeholder.
        window.location.href = '/start?new=1';
    }

    async function deleteUser(userId, name, hasData) {
        // Prompt only if profile has results or pipeline is running.
        // Failed/incomplete profiles clear silently.
        if (hasData && !confirm(`Delete profile "${name}"?`)) return;
        const data = await Api.deleteUser(userId);
        if (data.ok) window.location.href = data.redirect;
    }

    return { init, toggle, loadUsers, switchUser, addNewUser, deleteUser };
})();

// Bind on DOM ready
if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', () => UserPanel.init());
}
if (typeof module !== 'undefined' && module.exports) { module.exports = UserPanel; }
