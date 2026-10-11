/** Public market-data endpoint only; never put credentials in this URL. */
export function marketSocketUrl(configured:string|undefined,page:{protocol:string;host:string}):string {
  if (!configured) return `${page.protocol==="https:" ? "wss":"ws"}://${page.host}/stream/v1/market`;
  const url=new URL(configured);
  if (!['ws:','wss:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname!=="/ws/v1/market") {
    throw new Error("Market socket must be a credential-free ws(s) endpoint at /ws/v1/market");
  }
  if (page.protocol==="https:" && url.protocol!=="wss:") throw new Error("HTTPS pages require a secure market socket");
  return url.href;
}
