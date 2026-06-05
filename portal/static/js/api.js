/**
 * API Client (Model layer)
 * All server communication goes through here.
 * ViewModels call Api.*, never fetch() directly.
 */
const Api = (() => {
    async function _json(url, opts = {}) {
        const resp = await fetch(url, opts);
        return resp.json();
    }
    function _post(url, body) {
        return _json(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    }
    function _postForm(url, formData) {
        return _json(url, { method: 'POST', body: formData });
    }

    return {
        // Users
        getUsers:       ()                  => _json('/api/users'),
        createUser:     (name)              => _post('/api/users/create', { name }),
        switchUser:     (id)                => _post(`/api/users/switch/${id}`, {}),
        deleteUser:     (id)                => _post(`/api/users/delete/${id}`, {}),

        // Pipeline
        scanResume:     (formData)          => _postForm('/api/scan-resume', formData),
        startSearch:    (formData)          => _postForm('/api/start-search', formData),
        searchStatus:   ()                  => _json('/api/search-status'),
        refresh:        ()                  => _post('/api/refresh', {}),
        refreshStatus:  ()                  => _json('/api/refresh-status'),
        cancelPipeline: ()                  => _post('/api/cancel-pipeline', {}),

        // Settings
        checkOllama:    ()                  => _json('/api/check-ollama'),
        getProviders:   ()                  => _json('/api/providers'),
        setProvider:    (provider)          => _post('/api/set-provider', { provider }),
        setApiKey:      (key)               => _post('/api/set-api-key', { key }),
        setGroqKey:     (key)               => _post('/api/set-groq-key', { key }),

        // Documents
        generateResume: (data)              => _post('/api/generate-resume', data),
        generateCover:  (data)              => _post('/api/generate-cover-letter', data),

        // Search Profiles
        getSearchProfiles:  ()              => _json('/api/search-profiles'),
        createSearchProfile: (data)         => _post('/api/search-profiles/create', data),
        updateSearchProfile: (id, data)     => _post(`/api/search-profiles/update/${id}`, data),
        switchSearchProfile: (id)           => _post(`/api/search-profiles/switch/${id}`, {}),
        deleteSearchProfile: (id)           => _post(`/api/search-profiles/delete/${id}`, {}),

        // Sources
        getSources:     ()                  => _json('/api/sources'),
        addSource:      (source)            => _post('/api/sources/add', source),
        removeSource:   (name)              => _post('/api/sources/remove', { name }),
        toggleSource:   (name, enabled)     => _post('/api/sources/toggle', { name, enabled }),
    };
})();
