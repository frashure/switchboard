// Where the Gateway is. Same origin as the page by default -- the Gateway
// serves this app in deployment, and `npm run dev` proxies to it -- with a
// ?gateway=host:port override for pointing a dev build at another Gateway.

export interface GatewayEndpoints {
  http: string;
  ws: string;
}

export function resolveEndpoints(loc: Pick<Location, 'protocol' | 'host' | 'search'> = window.location): GatewayEndpoints {
  const secure = loc.protocol === 'https:';
  const host = new URLSearchParams(loc.search).get('gateway') || loc.host;
  return {
    http: `${secure ? 'https' : 'http'}://${host}`,
    ws: `${secure ? 'wss' : 'ws'}://${host}/ws`,
  };
}
