import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// Recharts' ResponsiveContainer needs ResizeObserver, which jsdom lacks.
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
URL.createObjectURL ??= () => 'blob:test';
URL.revokeObjectURL ??= () => {};

afterEach(cleanup);
