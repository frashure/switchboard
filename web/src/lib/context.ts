import { getContext, setContext } from 'svelte';
import type { SessionStore } from './session.svelte';

const KEY = Symbol('session');

export const provideSession = (session: SessionStore) => setContext(KEY, session);
export const useSession = () => getContext<SessionStore>(KEY);
