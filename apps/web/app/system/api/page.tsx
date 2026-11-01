import ApiConnections from './api-connections';

export const dynamic='force-dynamic';

export default function ApiSetupPage(){
 return <ApiConnections frontend={{
  hosted:Boolean(process.env.VERCEL),
  apiConfigured:Boolean(process.env.API_BASE_URL),
  directionalConfigured:Boolean(process.env.DIRECTIONAL_API_BASE_URL),
  goldConfigured:Boolean(process.env.GOLD_API_BASE_URL||process.env.API_BASE_URL),
 }}/>;
}
