import { describe, expect, it } from 'vitest';
import { resolveEndpoints } from '../src/lib/config';

describe('resolveEndpoints', () => {
  it('follows the page origin and scheme', () => {
    expect(resolveEndpoints({ protocol: 'https:', host: 'switchboard.ts.net', search: '' })).toEqual({
      http: 'https://switchboard.ts.net',
      ws: 'wss://switchboard.ts.net/ws',
    });
    expect(resolveEndpoints({ protocol: 'http:', host: 'localhost:5173', search: '' }).ws).toBe('ws://localhost:5173/ws');
  });

  it('honours a ?gateway= override', () => {
    expect(resolveEndpoints({ protocol: 'http:', host: 'localhost:5173', search: '?gateway=box:8090' })).toEqual({
      http: 'http://box:8090',
      ws: 'ws://box:8090/ws',
    });
  });
});
