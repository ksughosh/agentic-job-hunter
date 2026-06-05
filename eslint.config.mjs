// ESLint flat config — vanilla JS, browser globals.
export default [
  {
    files: ['portal/static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: {
        window: 'readonly',
        document: 'readonly',
        fetch: 'readonly',
        FormData: 'readonly',
        URLSearchParams: 'readonly',
        localStorage: 'readonly',
        navigator: 'readonly',
        console: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        alert: 'readonly',
        confirm: 'readonly',
        prompt: 'readonly',
        module: 'readonly',
        require: 'readonly',
        Chart: 'readonly',
        // Cross-module globals (IIFE pattern)
        Api: 'readonly',
        DashboardVM: 'readonly',
        OnboardingVM: 'readonly',
        UserPanel: 'readonly',
        ResumeBuilder: 'readonly',
        SearchProfiles: 'readonly',
        SourcesVM: 'readonly',
      },
    },
    rules: {
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-undef': 'error',
      // IIFE-defined module names are listed in globals so consumers compile;
      // disable builtinGlobals so the owning file's `const X = ...` doesn't trip.
      'no-redeclare': ['error', { builtinGlobals: false }],
      'no-empty': ['warn', { allowEmptyCatch: true }],
    },
  },
  {
    files: ['portal/static/js/__tests__/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        Blob: 'readonly',
        FormData: 'readonly',
        fetch: 'readonly',
        describe: 'readonly',
        it: 'readonly',
        test: 'readonly',
        expect: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
        vi: 'readonly',
        global: 'readonly',
      },
    },
  },
];
