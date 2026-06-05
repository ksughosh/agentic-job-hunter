import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['portal/static/js/__tests__/**/*.test.js'],
    globals: true,
  },
});
